import logging
from datetime import datetime
import config

import discord
from discord.ext import commands
import os
import json
import subprocess
import asyncio
from threading import Thread

from tracker import VoiceTimeTracker
from database import VoiceTrackerDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voice-tracker")

if not config.BOT_TOKEN:
    logger.error("BOT_TOKEN not set. Set it via environment variable or in a .env file.")
    raise SystemExit("Missing BOT_TOKEN")

class GitHubBackup:
    def __init__(self, repo_path=".", branch="main"):
        self.repo_path = repo_path
        self.branch = branch
        
    def git_command(self, command):
        """Execute git command and return result"""
        try:
            result = subprocess.run(
                command, 
                shell=True, 
                cwd=self.repo_path,
                capture_output=True, 
                text=True,
                timeout=30
            )
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            return False, "", str(e)
    
    def setup_git(self):
        """Setup git user config if not set"""
        # Check if git user is configured
        success, stdout, stderr = self.git_command("git config user.name")
        if not success or not stdout.strip():
            self.git_command('git config user.name "Voice Tracker Bot"')
            self.git_command('git config user.email "bot@discord-tracker.com"')
    
    def create_backup(self):
        """Create database backup and commit to GitHub"""
        try:
            # Check if we're in a git repo
            success, stdout, stderr = self.git_command("git status")
            if not success:
                logger.error("Not a git repository or git not available")
                return False, "Not a git repository"
            
            # Create backup directory if it doesn't exist
            backup_dir = "backups"
            os.makedirs(backup_dir, exist_ok=True)
            
            # Create timestamp for backup file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{backup_dir}/voice_tracker_backup_{timestamp}.db"
            
            # Copy database file
            if os.path.exists('voice_tracker.db'):
                import shutil
                shutil.copy2('voice_tracker.db', backup_file)
                logger.info(f"✅ Database backed up to: {backup_file}")
            else:
                return False, "Database file not found"
            
            # Also create JSON export
            db = VoiceTrackerDatabase()
            json_backup_file = f"{backup_dir}/data_export_{timestamp}.json"
            
            export_data = {
                'export_date': datetime.now().isoformat(),
                'voice_users': db.get_top_voice_users(limit=1000),
                'streamers': db.get_top_streamers(limit=1000)
            }
            
            with open(json_backup_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            # Add files to git
            self.git_command(f"git add {backup_file}")
            self.git_command(f"git add {json_backup_file}")
            
            # Commit
            commit_message = f"🤖 Automated backup {timestamp}"
            success, stdout, stderr = self.git_command(f'git commit -m "{commit_message}"')
            
            if not success:
                logger.warning("Nothing to commit or commit failed")
                return True, "No changes to commit"
            
            # Push to GitHub
            success, stdout, stderr = self.git_command(f"git push origin {self.branch}")
            
            if success:
                logger.info("✅ Backup pushed to GitHub successfully")
                return True, f"Backup completed and pushed to GitHub: {backup_file}"
            else:
                logger.error(f"❌ Failed to push to GitHub: {stderr}")
                return False, f"Failed to push to GitHub: {stderr}"
                
        except Exception as e:
            logger.error(f"❌ Backup failed: {e}")
            return False, f"Backup failed: {str(e)}"

class VoiceTrackerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.messages = True
        intents.guilds = True
        intents.message_content = True

        super().__init__(command_prefix='!vt ', intents=intents)

        # Initialize components
        self.database = VoiceTrackerDatabase(db_path=config.DB_PATH)
        self.tracker = VoiceTimeTracker(self.database)
        self.github_backup = GitHubBackup()
        
        # Setup git
        self.github_backup.setup_git()

    async def on_ready(self):
        logger.info(f"✅ {self.user} has connected to Discord!")
        logger.info(f"📊 Voice Tracker is monitoring {len(self.guilds)} server(s):")
        for guild in self.guilds:
            logger.info(f"   - {guild.name} (ID: {guild.id})")
        logger.info("🤖 Use !vt bot_help for commands")
        
        # Start automated backup task
        self.backup_task = self.loop.create_task(self.auto_backup())

    async def auto_backup(self):
        """Automatically backup every 6 hours"""
        await self.wait_until_ready()
        
        while not self.is_closed():
            try:
                # Wait 6 hours (21600 seconds)
                await asyncio.sleep(21600)
                
                logger.info("🕒 Starting automated backup...")
                success, message = self.github_backup.create_backup()
                
                if success:
                    logger.info("✅ Automated backup completed")
                else:
                    logger.warning(f"⚠️ Automated backup failed: {message}")
                    
            except Exception as e:
                logger.error(f"❌ Auto-backup task error: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour before retrying

    async def on_voice_state_update(self, member, before, after):
        # delegate to tracker
        await self.tracker.handle_voice_state_update(member, before, after)


# ---------------------
# Commands (including new backup commands)
# ---------------------
@commands.command()
async def bot_help(ctx):
    """Show available commands"""
    embed = discord.Embed(
        title="🎧 Voice & Stream Tracker Help",
        description="Track voice channel time and streaming statistics",
        color=0x7289DA
    )
    embed.add_field(name="!vt topstreamers", value="Show top 5 streamers", inline=False)
    embed.add_field(name="!vt topvoice", value="Show top 5 voice channel users", inline=False)
    embed.add_field(name="!vt mystats", value="Show your personal statistics", inline=False)
    embed.add_field(name="!vt debug", value="Debug database information", inline=False)
    embed.add_field(name="!vt backup", value="Manual database backup to GitHub", inline=False)
    embed.add_field(name="!vt bot_help", value="Show this help message", inline=False)

    await ctx.send(embed=embed)

@commands.command()
@commands.is_owner()
async def backup(ctx):
    """Manual database backup to GitHub (Owner only)"""
    await ctx.send("🔄 Starting manual backup to GitHub...")
    
    def run_backup():
        return ctx.bot.github_backup.create_backup()
    
    # Run backup in thread to avoid blocking
    try:
        success, message = await asyncio.get_event_loop().run_in_executor(None, run_backup)
        
        if success:
            embed = discord.Embed(
                title="✅ Backup Successful",
                description=message,
                color=0x00ff00,
                timestamp=datetime.now()
            )
        else:
            embed = discord.Embed(
                title="❌ Backup Failed",
                description=message,
                color=0xff0000,
                timestamp=datetime.now()
            )
            
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Backup error: {str(e)}")

@commands.command()
@commands.is_owner()
async def backup_status(ctx):
    """Check backup status and git configuration"""
    # Check git status
    success, stdout, stderr = ctx.bot.github_backup.git_command("git status")
    remote_success, remote_stdout, remote_stderr = ctx.bot.github_backup.git_command("git remote -v")
    
    embed = discord.Embed(
        title="🔧 Backup Status",
        color=0x7289DA,
        timestamp=datetime.now()
    )
    
    if success:
        embed.add_field(name="Git Status", value="✅ Repository configured", inline=True)
    else:
        embed.add_field(name="Git Status", value="❌ Not a git repository", inline=True)
    
    if remote_success and remote_stdout:
        embed.add_field(name="Remote", value="✅ GitHub remote configured", inline=True)
    else:
        embed.add_field(name="Remote", value="❌ No remote configured", inline=True)
    
    # Check if database file exists
    if os.path.exists('voice_tracker.db'):
        size = os.path.getsize('voice_tracker.db')
        embed.add_field(name="Database", value=f"✅ {size/1024/1024:.2f} MB", inline=True)
    else:
        embed.add_field(name="Database", value="❌ Not found", inline=True)
    
    await ctx.send(embed=embed)

# ... keep your existing commands (topstreamers, topvoice, mystats, debug) exactly as they are ...

# ---------------------
# Run
# ---------------------
if __name__ == "__main__":
    bot = VoiceTrackerBot()

    # Add commands
    bot.add_command(bot_help)
    bot.add_command(topstreamers)
    bot.add_command(topvoice)
    bot.add_command(mystats)
    bot.add_command(debug)
    bot.add_command(backup)
    bot.add_command(backup_status)

    @bot.event
    async def on_message(message):
        # ignore bot's own messages
        if message.author == bot.user:
            return

        # Debug log for commands
        if message.content.startswith('!vt '):
            logger.info(f"📨 Command received: '{message.content}' from {message.author}")

        await bot.process_commands(message)

    logger.info("🚀 Starting Discord Voice & Stream Tracker...")
    try:
        bot.run(config.BOT_TOKEN)
    except Exception as e:
        logger.exception("Bot terminated with an exception: %s", e)
