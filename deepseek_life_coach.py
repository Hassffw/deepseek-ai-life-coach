import json
import os
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import openai
import psycopg2
import psycopg2.extras

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_BASE = os.getenv('DEEPSEEK_API_BASE', "https://api.deepseek.com/v1")
DATABASE_URL = os.getenv('DATABASE_URL')
COACHING_RATE_LIMIT = timedelta(hours=1)
# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

class DeepSeekAPI:
    def __init__(self, api_key):
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_API_BASE
        )

    async def generate_response(self, prompt):
        try:
            response = await self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "You are an empathetic and professional life coach."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return "Sorry, I'm currently experiencing technical difficulties. Please try again later."

class LifeCoachBot:
    def __init__(self):
        self.deepseek = DeepSeekAPI(DEEPSEEK_API_KEY)
        self.conn = None  # Database connection
        self.job_queue = None

    def connect_db(self):
        """Connect to the PostgreSQL database."""
        try:
            self.conn = psycopg2.connect(DATABASE_URL,
                                         sslmode='disable')  # Change sslmode to 'disable'
            self.conn.autocommit = True  # set autocommit to True
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1")
            logger.info("Database connection established.")
            self.create_tables()
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    def create_tables(self):
        """Create necessary tables if they don't exist."""
        try:
            cursor = self.conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id VARCHAR(255) PRIMARY KEY,
                    preferences JSONB
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) REFERENCES users(user_id),
                    goal_text TEXT,
                    date_set TIMESTAMP,
                    status VARCHAR(255),
                    priority VARCHAR(255),
                    deadline TIMESTAMP,
                    category VARCHAR(255)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mood_log (
                    mood_id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) REFERENCES users(user_id),
                    mood VARCHAR(255),
                    original_text TEXT,
                    timestamp TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS coaching_history (
                    coaching_id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) REFERENCES users(user_id),
                    timestamp TIMESTAMP,
                    prompt TEXT,
                    response TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255),
                    job_id VARCHAR(255),
                    time VARCHAR(255),
                    next_run_time TIMESTAMP,
                    type VARCHAR(255)  -- e.g., 'mood'
                )
            """)

            self.conn.commit()
            logger.info("Tables created/verified.")

        except Exception as e:
            logger.error(f"Table creation failed: {e}")
            raise

    def initialize(self, application: Application):
        """Initializes the bot: connects to the database, loads reminders."""
        try:
            self.connect_db()
            self.job_queue = application.job_queue  # Store the application's job_queue
            self.load_reminders(application)  # Pass application to load_reminders
            logger.info("Bot initialization complete.")
        except Exception as e:
            logger.critical(f"Bot initialization failed: {e}")
            raise  # Exit if initialization fails

    def get_user_profile(self, user_id):
        """Gets the user profile from the database."""
        try:
            cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT preferences FROM users WHERE user_id = %s", (str(user_id),))
            result = cursor.fetchone()

            if result:
                profile = result[0] # Get existing profile
                if profile is None: # Handle case where preferences is NULL in DB
                    profile = {}
                return profile
            else:
                # Create a default profile if it doesn't exist
                default_profile = {
                    "goals": [],
                    "mood_log": [],
                    "coaching_history": [],
                    "preferences": {},
                    "last_interaction": None,
                    "last_coaching": None,
                    "reminders": {},
                    "state": 'idle' # Add 'state' to default profile
                }
                cursor.execute("INSERT INTO users (user_id, preferences) VALUES (%s, %s)",
                               (str(user_id), json.dumps(default_profile)))
                self.conn.commit()
                return default_profile

        except Exception as e:
            logger.error(f"Error getting/creating user profile: {e}")
            return {  # Return a default profile in case of an error
                "goals": [],
                "mood_log": [],
                "coaching_history": [],
                "preferences": {},
                "last_interaction": None,
                "last_coaching": None,
                "reminders": {},
                "state": 'idle' # Add 'state' to error default profile
            }

    def save_user_profile(self, user_id, user_profile):
        """Saves the user profile to the database."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE users SET preferences = %s WHERE user_id = %s",
                           (json.dumps(user_profile), str(user_id)))
            self.conn.commit()
            logger.info(f"User profile saved for user {user_id}")
        except Exception as e:
            logger.error(f"Error saving user profile: {e}")

    def load_reminders(self, application: Application):
        """Loads and schedules reminders from the database."""
        try:
            cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT user_id, job_id, time, next_run_time, type FROM reminders")
            reminders = cursor.fetchall()

            for reminder in reminders:
                try:
                    user_id = reminder["user_id"]
                    job_id = reminder["job_id"]
                    time_str = reminder["time"]
                    next_run_time = reminder["next_run_time"]
                    reminder_type = reminder["type"]

                    if reminder_type == "mood":
                        # removed run_repeating for mood reminders
                        logger.info(f"Mood reminder loading skipped for user {user_id} with job ID {job_id} - Functionality removed.")

                except (ValueError, KeyError) as e:
                    logger.error(f"Error loading reminder for user {user_id}: {e}")
                    self.delete_reminder_from_db(user_id, job_id) #delete the reminder if it cannot be loaded

        except Exception as e:
            logger.error(f"Error loading reminders: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)  # Ensure profile exists
        user_profile['state'] = 'idle' # Initialize user state
        self.save_user_profile(user_id, user_profile)

        keyboard = [
            ['/goal', '/mood', '/progress'],
            ['/coaching', '/help'],
            ['/editgoal', '/deletegoal'],
            ['/editmood', '/deletemood']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        welcome_message = (
            "👋 Welcome to your personal AI Life Coach!\n\n"
            "I am here to help you achieve your goals and improve your life.\n\n"
            "Tap a command button below or type /help for a full list of commands."
        )
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)

    async def set_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'setting_goal' # Set state to 'setting_goal'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Okay, what is your new goal? Please type it in.") # Prompt for goal text

    async def edit_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'editing_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Which goal number do you want to edit? Please type the number.")

    async def delete_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'deleting_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Which goal number do you want to delete? Please type the number.")

    async def complete_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'completing_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Which goal number do you want to complete? Please type the number.")

    async def prioritize_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'prioritizing_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Which goal number do you want to prioritize? Please type the number.")

    async def set_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'setting_deadline_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("For which goal number do you want to set a deadline? Please type the number.")

    async def set_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'setting_category_goal_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("For which goal number do you want to set a category? Please type the number.")

    async def log_mood(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'logging_mood'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("How are you feeling? Please describe your mood.")

    async def edit_mood(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'editing_mood_number'
        self.save_user_profile(user_id, user_profile)
        logger.info(f"User {user_id} entered /editmood. State set to editing_mood_number.") # Log state setting
        await update.message.reply_text("Which mood entry number do you want to edit? Please type the number.")

    async def delete_mood(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        user_profile['state'] = 'deleting_mood_number'
        self.save_user_profile(user_id, user_profile)
        await update.message.reply_text("Which mood entry number do you want to delete? Please type the number.")

    async def coaching_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)

        # Rate limiting and actual coaching session logic remain the same
        # ...

        # No state change needed for coaching as it doesn't require follow-up input after command

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_profile = self.get_user_profile(user_id)
        user_state = user_profile.get('state', 'idle') # Get user state, default to 'idle'
        logger.info(f"Handle message from user {user_id}. Current state: {user_state}") # Log current state

        if user_state == 'setting_goal': # Check if user is setting a goal
            goal_text = update.message.text
            if goal_text:
                if any(word in goal_text.lower() for word in ["happier", "better", "improve"]):
                    await update.message.reply_text("That's a great goal! To make it more tangible, could you formulate it more specifically?")
                    return # Keep user in 'setting_goal' state to allow re-entry

                try:
                    cursor = self.conn.cursor()
                    cursor.execute("""
                        INSERT INTO goals (user_id, goal_text, date_set, status, priority, deadline, category)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (str(user_id), goal_text, datetime.now(), "active", "medium", None, None)) # Explicitly format datetime and user_id as string
                    self.conn.commit()
                    await update.message.reply_text(f"✅ New goal set: {goal_text}")
                except Exception as e:
                    logger.error(f"Error setting goal: {e}")
                    await update.message.reply_text("Error setting the goal. Please try again later.")
            else:
                await update.message.reply_text("Please specify a goal. Example: Exercise more") # Re-prompt if no goal text

            user_profile['state'] = 'idle' # Reset user state after goal is processed (or error)
            self.save_user_profile(user_id, user_profile)
            return # Important: Exit handle_message after processing goal

        elif user_state == 'editing_goal_number':

            try:
                goal_id = int(update.message.text)
                user_profile['state'] = 'editing_goal_text' # Next state: get new goal text
                user_profile['goal_id_to_edit'] = goal_id # Store goal_id for next step
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text(f"Okay, you want to edit goal number {goal_id}. What is the new text for this goal?")
                logger.info(f"User {user_id} provided goal number {goal_id} to edit. State set to editing_goal_text.") # Log state transition
                return
            except ValueError:
                user_profile['state'] = 'idle'
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text("Invalid goal number. Please type a number.")
                logger.warning(f"User {user_id} provided invalid goal number. State reset to idle.") # Log invalid input and state reset
                return

        elif user_state == 'editing_goal_text':
            goal_id = user_profile.get('goal_id_to_edit')
            new_goal_text = update.message.text
            if goal_id and new_goal_text:
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("UPDATE goals SET goal_text = %s WHERE goal_id = %s AND user_id = %s", (new_goal_text, goal_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Goal not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"✅ Goal {goal_id} updated.")
                except Exception as e:
                    logger.error(f"Error editing goal: {e}")
                    await update.message.reply_text("Error editing the goal. Please try again later.")
            else:
                await update.message.reply_text("Error processing goal edit. Please try again.")

            user_profile['state'] = 'idle'
            user_profile.pop('goal_id_to_edit', None) # Clean up stored goal_id
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'deleting_goal_number':
            try:
                goal_id = int(update.message.text)
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("DELETE FROM goals WHERE goal_id = %s AND user_id = %s", (goal_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Goal not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"✅ Goal {goal_id} deleted.")
                except Exception as e:
                    logger.error(f"Error deleting goal: {e}")
                    await update.message.reply_text("Error deleting the goal. Please try again later.")
            except ValueError:
                await update.message.reply_text("Invalid goal number. Please type a number.")

            user_profile['state'] = 'idle'
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'completing_goal_number':
            try:
                goal_id = int(update.message.text)
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("UPDATE goals SET status = 'completed' WHERE goal_id = %s AND user_id = %s", (goal_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Goal not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"🎉 Goal {goal_id} marked as completed!")
                except Exception as e:
                    logger.error(f"Error completing goal: {e}")
                    await update.message.reply_text("Error marking the goal as completed. Please try again later.")
            except ValueError:
                await update.message.reply_text("Invalid goal number. Please type a number.")

            user_profile['state'] = 'idle'
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'prioritizing_goal_number':
            try:
                goal_id = int(update.message.text)
                user_profile['state'] = 'setting_priority_value' # Next state: get priority value
                user_profile['goal_id_to_prioritize'] = goal_id # Store goal_id
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text(f"Okay, you want to prioritize goal number {goal_id}. What priority do you want to set (high, medium, low)?")
                return
            except ValueError:
                user_profile['state'] = 'idle'
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text("Invalid goal number. Please type a number.")
                return

        elif user_state == 'setting_priority_value':
            goal_id = user_profile.get('goal_id_to_prioritize')
            priority = update.message.text.lower()
            if goal_id and priority in ["high", "medium", "low"]:
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("UPDATE goals SET priority = %s WHERE goal_id = %s AND user_id = %s", (priority, goal_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Goal not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"✅ Priority of goal {goal_id} set to {priority}.")
                except Exception as e:
                    logger.error(f"Error prioritizing goal: {e}")
                    await update.message.reply_text("Error setting the priority. Please try again later.")
            else:
                await update.message.reply_text("Invalid priority value. Please provide: high, medium, or low.")

            user_profile['state'] = 'idle'
            user_profile.pop('goal_id_to_prioritize', None) # Clean up stored goal_id
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'setting_deadline_goal_number':
            try:
                goal_id = int(update.message.text)
                user_profile['state'] = 'setting_deadline_date' # Next state: get deadline date
                user_profile['goal_id_for_deadline'] = goal_id # Store goal_id
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text(f"Okay, for goal number {goal_id}, what deadline do you want to set? Please use YYYY-MM-DD format.")
                return
            except ValueError:
                user_profile['state'] = 'idle'
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text("Invalid goal number. Please type a number.")
                return

        elif user_state == 'setting_deadline_date':
            goal_id = user_profile.get('goal_id_for_deadline')
            deadline_str = update.message.text
            try:
                deadline = datetime.strptime(deadline_str, "%Y-%m-%d")
                if goal_id:
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute("UPDATE goals SET deadline = %s WHERE goal_id = %s AND user_id = %s", (deadline, goal_id, user_id))
                        if cursor.rowcount == 0:
                            await update.message.reply_text("Goal not found or does not belong to this user.")
                        else:
                            self.conn.commit()
                            await update.message.reply_text(f"✅ Deadline for goal {goal_id} set to {deadline_str}.")
                    except Exception as e:
                        logger.error(f"Error setting deadline: {e}")
                        await update.message.reply_text("Error setting the deadline. Please try again later.")
                else:
                    await update.message.reply_text("Error processing deadline. Please try again.")
            except ValueError:
                await update.message.reply_text("Invalid date format. Please use YYYY-MM-DD.")

            user_profile['state'] = 'idle'
            user_profile.pop('goal_id_for_deadline', None) # Clean up stored goal_id
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'setting_category_goal_number':
            try:
                goal_id = int(update.message.text)
                user_profile['state'] = 'setting_category_text' # Next state: get category text
                user_profile['goal_id_for_category'] = goal_id # Store goal_id
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text(f"Okay, for goal number {goal_id}, what category do you want to set? Please type the category name.")
                return
            except ValueError:
                user_profile['state'] = 'idle'
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text("Invalid goal number. Please type a number.")
                return

        elif user_state == 'setting_category_text':
            goal_id = user_profile.get('goal_id_for_category')
            category = update.message.text
            if goal_id and category:
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("UPDATE goals SET category = %s WHERE goal_id = %s AND user_id = %s", (category, goal_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Goal not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"✅ Category for goal {goal_id} set to '{category}'.")
                except Exception as e:
                    logger.error(f"Error setting category: {e}")
                    await update.message.reply_text("Error setting the category. Please try again later.")
            else:
                await update.message.reply_text("Error processing category. Please try again.")

            user_profile['state'] = 'idle'
            user_profile.pop('goal_id_for_category', None) # Clean up stored goal_id
            self.save_user_profile(user_id, user_profile)
            return

        elif user_state == 'editing_mood_number':
            logger.info("Entering elif user_state == 'editing_mood_number' block") # Added log: Check if we enter this block
            try:
                mood_id = int(update.message.text)
                logger.info(f" mood_id parsed: {mood_id}") # Log parsed mood_id
                user_profile['state'] = 'editing_mood_text' # Next state: get new mood text
                user_profile['mood_id_to_edit'] = mood_id # Store mood_id for next step
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text(f"Okay, you want to edit mood entry number {mood_id}. What is the new text for this mood?")
                logger.info(f"User {user_id} provided mood number {mood_id} to edit. State set to editing_mood_text.") # Log state transition
                return # ENSURE RETURN HERE
            except ValueError:
                logger.warning(" ValueError: Invalid mood number entered.") # Log ValueError
                user_profile['state'] = 'idle'
                self.save_user_profile(user_id, user_profile)
                await update.message.reply_text("Invalid mood entry number. Please type a number.")
                logger.warning(f"User {user_id} provided invalid mood number. State reset to idle.") # Log invalid input and state reset
                return # ENSURE RETURN HERE

        elif user_state == 'editing_mood_text':
            mood_id = user_profile.get('mood_id_to_edit')
            new_mood_text = update.message.text
            if mood_id and new_mood_text:
                standard_moods = {
                    "happy": ["happy", "joyful", "great", "good", "glücklich", "froh", "very good", "verygood", "awesome", "awesom"], # Added "awesome", "awesom"
                    "sad": ["sad", "depressed", "down", "traurig", "niedergeschlagen"],
                    "neutral": ["neutral", "okay", "meh", "normal"],
                    "angry": ["angry", "frustrated", "irritated", "wütend", "verärgert"]
                }
                normalized_mood = "neutral"
                for mood, synonyms in standard_moods.items():
                    if new_mood_text.lower() in synonyms:
                        normalized_mood = mood
                        break # Added break to exit loop once mood is found

                try:
                    cursor = self.conn.cursor()
                    cursor.execute("UPDATE mood_log SET mood = %s, original_text = %s WHERE mood_id = %s AND user_id = %s", (normalized_mood, new_mood_text, mood_id, user_id))
                    if cursor.rowcount == 0:
                        await update.message.reply_text("Mood entry not found or does not belong to this user.")
                    else:
                        self.conn.commit()
                        await update.message.reply_text(f"✅ Mood {mood_id} updated.")
                except Exception as e:
                    logger.error(f"Error editing mood: {e}")
                    await update.message.reply_text("Error editing the mood. Please try again later.")
            else:
                await update.message.reply_text("Error processing mood edit. Please try again.")
                user_profile.pop('mood_id_to_edit', None) # Clean up stored mood_id

            user_profile['state'] = 'idle' # Reset state after mood editing
            self.save_user_profile(user_id, user_profile)
            return # ENSURE RETURN HERE

        elif user_state == 'logging_mood':
            mood_text = update.message.text
            if mood_text:
                standard_moods = {
                    "happy": ["happy", "joyful", "great", "good", "glücklich", "froh", "very good", "verygood", "awesome", "awesom"],
                    "sad": ["sad", "depressed", "down", "traurig", "niedergeschlagen"],
                    "neutral": ["neutral", "okay", "meh", "normal"],
                    "angry": ["angry", "frustrated", "irritated", "wütend", "verärgert"]
                }
                normalized_mood = "neutral"  # Default mood
                for mood, synonyms in standard_moods.items():
                    if mood_text.lower() in synonyms:
                        normalized_mood = mood
                        break

                try:
                    cursor = self.conn.cursor()
                    cursor.execute("""
                        INSERT INTO mood_log (user_id, mood, original_text, timestamp)
                        VALUES (%s, %s, %s, %s)
                    """, (str(user_id), normalized_mood, mood_text, datetime.now()))
                    self.conn.commit()
                    await update.message.reply_text(f"✅ Mood logged: {mood_text}")

                except Exception as e:
                    logger.error(f"Error logging mood: {e}")
                    await update.message.reply_text("Error logging mood. Please try again later.")
            else:
                await update.message.reply_text("Please describe your mood.")

            user_profile['state'] = 'idle'
            self.save_user_profile(user_id, user_profile)
            return


        elif user_state != 'idle': # Default for any non-idle state we haven't explicitly handled - COACHING RESPONSE
            try:
                cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cursor.execute("SELECT goal_text FROM goals WHERE user_id = %s", (user_id,))
                goals = cursor.fetchall()
                goals_text = "\n".join([goal['goal_text'] for goal in goals])

                prompt = f"""
                    User seems to be in state: {user_state}.
                    User's previous goals: {goals_text}
                    Current message: {update.message.text}

                    Please respond as an empathetic life coach and refer to the user's goals and current state, gently guiding them back to using commands or clarifying their input.
                """
                response = await self.deepseek.generate_response(prompt)
                await update.message.reply_text(response)
            except Exception as e:
                logger.error(f"Error handling message in non-idle state: {e}")
                await update.message.reply_text("I'm having a little trouble understanding. Could you please use a command or clarify what you meant?")
            return # ENSURE RETURN HERE


        else: # user_state == 'idle' - DEFAULT MESSAGE HANDLING (IDLE STATE)
            try:
                cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cursor.execute("SELECT goal_text FROM goals WHERE user_id = %s", (user_id,))
                goals = cursor.fetchall()
                goals_text = "\n".join([goal['goal_text'] for goal in goals])

                prompt = f"""
                    User's previous goals: {goals_text}
                    Current message: {update.message.text}

                    Please respond as an empathetic life coach and refer to the user's goals.
                """
                response = await self.deepseek.generate_response(prompt)
                await update.message.reply_text(response)
            except Exception as e:
                logger.error(f"Error handling message in idle state: {e}")
                await update.message.reply_text("I did not understand this message. Please use the available commands or rephrase your request. For a list of commands, type /help.")
            return # ENSURE RETURN HERE

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE): # renamed from help to help_command
        keyboard = [
            ['/goal', '/mood', '/progress'], # First row: core actions
            ['/coaching', '/help'],         # Second row: coaching and help
            ['/editgoal', '/deletegoal'],   # Third row: goal management
            ['/editmood', '/deletemood'],    # Fourth row: mood management
            ['/completegoal', '/prioritize', '/setdeadline', '/setcategory'] # Fifth row: more goal options
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        help_message = (
            "Here are the available commands:\n"
            "/goal - Set new goal\n"
            "/editgoal - Edit goal\n"
            "/deletegoal - Delete goal\n"
            "/completegoal - Mark goal as completed\n"
            "/prioritize - Prioritize goal (high, medium, low)\n"
            "/setdeadline - Set deadline for a goal\n"
            "/setcategory - Set category for a goal\n"
            "/mood - Log mood\n"
            "/editmood - Edit mood\n"
            "/deletemood - Delete mood\n"
            "/progress - Show progress\n"
            "/help - Show this message\n"
            "/coaching - Start a coaching session"
        )
        await update.message.reply_text(help_message, reply_markup=reply_markup)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE): # english alias for /hilfe
        await self.help_command(update, context)

    async def show_progress(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        try:
            cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            # Fetch goals
            cursor.execute("SELECT goal_text, status, priority, deadline, category, goal_id FROM goals WHERE user_id = %s", (user_id,))
            goals = cursor.fetchall()

            # Fetch mood log (last 5 entries)
            cursor.execute("""
                SELECT mood, original_text, timestamp, mood_id
                FROM mood_log
                WHERE user_id = %s
                ORDER BY timestamp DESC
                LIMIT 5
            """, (user_id,))
            mood_entries = cursor.fetchall()

            progress_message = "📊 Your Progress:\n\n"

            if goals:
                progress_message += "🎯 Goals:\n"
                for goal in goals:
                    status_emoji = "✅" if goal['status'] == 'completed' else "⏳"
                    deadline_text = f"Deadline: {goal['deadline'].strftime('%Y-%m-%d')}" if goal['deadline'] else "No Deadline"
                    category_text = f"Category: {goal['category']}" if goal['category'] else "No Category"
                    priority_text = f"Priority: {goal['priority']}" if goal['priority'] else "No Priority"

                    progress_message += (
                        f"  {status_emoji} Goal {goal['goal_id']}: {goal['goal_text']}\n"
                        f"     Status: {goal['status'].capitalize()}, {priority_text}, {deadline_text}, {category_text}\n"
                    )
                progress_message += "\n"
            else:
                progress_message += "No goals set yet.\n\n"

            if mood_entries:
                progress_message += "😊 Recent Moods (last 5 entries):\n"
                for mood_entry in mood_entries:
                    mood_emoji = {
                        "happy": "😄",
                        "sad": "😔",
                        "neutral": "😐",
                        "angry": "😠"
                    }.get(mood_entry['mood'], "❓") # Default emoji if mood is not recognized
                    progress_message += (
                        f"  {mood_emoji} Mood {mood_entry['mood_id']} ({mood_entry['timestamp'].strftime('%Y-%m-%d %H:%M')}): "
                        f"{mood_entry['mood'].capitalize()} - '{mood_entry['original_text']}'\n"
                    )
            else:
                progress_message += "No mood entries logged yet.\n"

            await update.message.reply_text(progress_message)

        except Exception as e:
            logger.error(f"Error showing progress: {e}")
            await update.message.reply_text("Error retrieving progress information. Please try again later.")

def main():
    bot = LifeCoachBot()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    try:
        bot.initialize(application)
    except Exception as e:
        logger.critical(f"Bot failed to initialize: {e}")
        return  # Exit if the bot cannot initialize

    # Handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("goal", bot.set_goal))
    application.add_handler(CommandHandler("editgoal", bot.edit_goal))
    application.add_handler(CommandHandler("deletegoal", bot.delete_goal))
    application.add_handler(CommandHandler("completegoal", bot.complete_goal))
    application.add_handler(CommandHandler("prioritize", bot.prioritize_goal))
    application.add_handler(CommandHandler("setdeadline", bot.set_deadline))
    application.add_handler(CommandHandler("setcategory", bot.set_category))
    application.add_handler(CommandHandler("mood", bot.log_mood))
    application.add_handler(CommandHandler("editmood", bot.edit_mood))
    application.add_handler(CommandHandler("deletemood", bot.delete_mood))
    application.add_handler(CommandHandler("progress", bot.show_progress))
    application.add_handler(CommandHandler("coaching", bot.coaching_session))
    application.add_handler(CommandHandler("help_command", bot.help_command)) # renamed from hilfe to help_command
    application.add_handler(CommandHandler("help", bot.help)) # english alias for /hilfe
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))

    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()