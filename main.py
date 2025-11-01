import logging
from datetime import datetime
import config

import discord
from discord.ext import commands
import os
import json
import subprocess
import asyncio
import shutil

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
        try:
            # Check if git user is configured
            success, stdout, stderr = self.git_command("git config user.name")
            if not success or not stdout.strip():
                self.git_command('git config user.name "Voice Tracker Bot"')
                self.git_command('git config user.email "bot@discord-tracker.com"')
                logger.info("✅ Git user configured")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Git setup warning: {e}")
            return False
    
    def create_backup(self):
        """Create database backup and commit to GitHub"""
        try:
            # Check if we're in a git repo
            success, stdout, stderr = self.git_command("git status")
            if not success:
                logger.error("Not a git repository or git not available")
                return False, "Not a git repository or git not available"
            
            # Create backup directory if it doesn't exist
            backup_dir = "backups"
            os.makedirs(backup_dir, exist_ok=True)
            
            # Create timestamp for backup file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{backup_dir}/voice_tracker_backup_{timestamp}.db"
            
            # Copy database file
            if os.path.exists('voice_tracker.db'):
                shutil.copy2('voice_tracker.db', backup_file)
                logger.info(f"✅ Database backed up to: {backup_file}")
            else:
                return False, "Database file not found"
            
            # Also create JSON export
            json_backup_file = f"{backup_dir}/data_export_{timestamp}.json"
            
            export_data = {
                'export_date': datetime.now().isoformat(),
                'backup_timestamp': timestamp,
                'voice_users_count': 0,
                'streamers_count': 0
            }
            
            try:
                db = VoiceTrackerDatabase()
                voice_users = db.get_top_voice_users(limit=1000)
                streamers = db.get_top_streamers(limit=1000)
                
                export_data.update({
                    'voice_users': voice_users,
                    'streamers': streamers,
                    'voice_users_count': len(voice_users),
                    'streamers_count': len(streamers)
                })
                
                with open(json_backup_file, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
                
                logger.info(f"✅ JSON export created: {json_backup_file}")
                
            except Exception as e:
                logger.warning(f"⚠️ JSON export failed, continuing with DB backup only: {e}")
                # Remove failed JSON file if it exists
                if os.path.exists(json_backup_file):
                    os.remove(json_backup_file)
                json_backup_file = None
            
            # Add files to git
            add_command = f"git add {backup_file}"
            if json_backup_file and os.path.exists(json_backup_file):
                add_command += f" {json_backup_file}"
            
            self.git_command(add_command)
            
            # Commit
            commit_message = f"🤖 Automated backup {timestamp}"
            success, stdout, stderr = self.git_command(f'git commit -m "{commit_message}"')
            
            if not success:
                logger.warning("Nothing to commit or commit failed")
                # Try pulling latest changes first
                self.git_command("git pull origin main")
                success, stdout, stderr = self.git_command(f'git commit -m "{commit_message}"')
                
                if not success:
                    return True, "No changes to commit or commit failed"
            
            # Push to GitHub
            success, stdout, stderr = self.git_command(f"git push origin {self.branch}")
            
            if success:
                logger.info("✅ Backup pushed to GitHub successfully")
                
                # Clean up old backups (keep last 10)
                self.cleanup_old_backups(backup_dir, keep_count=10)
                
                return True, f"Backup completed and pushed to GitHub\n• {backup_file}\n• {json_backup_file if json_backup_file else 'DB only'}"
            else:
                logger.error(f"❌ Failed to push to GitHub: {stderr}")
                return False, f"Failed to push to GitHub: {stderr}"
                
        except Exception as e:
            logger.error(f"❌ Backup failed: {e}")
            return False, f"Backup failed: {str(e)}"
    
    def cleanup_old_backups(self, backup_dir, keep_count=10):
        """Keep only the most recent backup files"""
        try:
            if not os.path.exists(backup_dir):
                return
                
            # Get all backup files
            db_files = [f for f in os.listdir(backup_dir) if f.startswith('voice_tracker_backup_') and f.endswith('.db')]
            json_files = [f for f in os.listdir(backup_dir) if f.startswith('data_export_') and f.endswith('.json')]
            
            # Sort by timestamp (newest first)
            db_files.sort(reverse=True)
            json_files.sort(reverse=True)
            
            # Remove old files
            for old_file in db_files[keep_count:]:
                os.remove(os.path.join(backup_dir, old_file))
                logger.info(f"🧹 Removed old backup: {old_file}")
                
            for old_file in json_files[keep_count:]:
                os.remove(os.path.join(backup_dir, old_file))
                logger.info(f"🧹 Removed old JSON export: {old_file}")
                
        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

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
        
        # Setup git (non-blocking)
        self.loop.create_task(self.setup_backup_system())

    async def setup_backup_system(self):
        """Setup backup system after bot is ready"""
        await asyncio.sleep(10)  # Wait a bit after startup
        self.github_backup.setup_git()
        logger.info("✅ Backup system initialized")

    async def on_ready(self):
        logger.info(f"✅ {self.user} has connected to Discord!")
        logger.info(f"📊 Voice Tracker is monitoring {len(self.guilds)} server(s):")
        for guild in self.guilds:
            logger.info(f"   - {guild.name} (ID: {guild.id})")
        logger.info("🤖 Use !vt bot_help for commands")
        
        # Start automated backup task (every 4 hours)
        self.backup_task = self.loop.create_task(self.auto_backup())

    async def auto_backup(self):
        """Automatically backup every 4 hours"""
        await self.wait_until_ready()
        await asyncio.sleep(60)  # Wait 1 minute after ready
        
        while not self.is_closed():
            try:
                logger.info("🕒 Starting automated backup...")
                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    self.github_backup.create_backup
                )
                
                if success:
                    logger.info("✅ Automated backup completed")
                else:
                    logger.warning(f"⚠️ Automated backup failed: {message}")
                    
                # Wait 4 hours (14400 seconds) before next backup
                await asyncio.sleep(14400)
                    
            except Exception as e:
                logger.error(f"❌ Auto-backup task error: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour before retrying

    async def on_voice_state_update(self, member, before, after):
        # delegate to tracker
        await self.tracker.handle_voice_state_update(member, before, after)


# ---------------------
# Commands
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
    embed.add_field(name="!vt backup_status", value="Check backup system status", inline=False)
    embed.add_field(name="!vt bot_help", value="Show this help message", inline=False)

    await ctx.send(embed=embed)

@commands.command()
@commands.is_owner()
async def backup(ctx):
    """Manual database backup to GitHub (Owner only)"""
    # Send initial message
    message = await ctx.send("🔄 Starting manual backup to GitHub...")
    
    def run_backup():
        return ctx.bot.github_backup.create_backup()
    
    # Run backup in thread to avoid blocking
    try:
        success, result_message = await asyncio.get_event_loop().run_in_executor(None, run_backup)
        
        if success:
            embed = discord.Embed(
                title="✅ Backup Successful",
                description=result_message,
                color=0x00ff00,
                timestamp=datetime.now()
            )
            # Add file sizes if available
            if os.path.exists('voice_tracker.db'):
                size = os.path.getsize('voice_tracker.db')
                embed.add_field(name="Database Size", value=f"{size/1024/1024:.2f} MB", inline=True)
        else:
            embed = discord.Embed(
                title="❌ Backup Failed",
                description=result_message,
                color=0xff0000,
                timestamp=datetime.now()
            )
            
        await message.edit(content="", embed=embed)
        
    except Exception as e:
        await message.edit(content=f"❌ Backup error: {str(e)}")

@commands.command()
@commands.is_owner()
async def backup_status(ctx):
    """Check backup status and git configuration"""
    # Check git status
    git_success, git_stdout, git_stderr = ctx.bot.github_backup.git_command("git status")
    remote_success, remote_stdout, remote_stderr = ctx.bot.github_backup.git_command("git remote -v")
    
    embed = discord.Embed(
        title="🔧 Backup System Status",
        color=0x7289DA,
        timestamp=datetime.now()
    )
    
    # Git status
    if git_success:
        embed.add_field(name="Git Repository", value="✅ Configured", inline=True)
    else:
        embed.add_field(name="Git Repository", value="❌ Not available", inline=True)
    
    # Remote status
    if remote_success and remote_stdout:
        embed.add_field(name="GitHub Remote", value="✅ Configured", inline=True)
    else:
        embed.add_field(name="GitHub Remote", value="❌ Not configured", inline=True)
    
    # Database status
    if os.path.exists('voice_tracker.db'):
        size = os.path.getsize('voice_tracker.db')
        embed.add_field(name="Database File", value=f"✅ {size/1024/1024:.2f} MB", inline=True)
    else:
        embed.add_field(name="Database File", value="❌ Not found", inline=True)
    
    # Backup directory status
    if os.path.exists('backups'):
        backup_files = len([f for f in os.listdir('backups') if f.endswith('.db')])
        embed.add_field(name="Backup Files", value=f"📁 {backup_files} backups", inline=True)
    else:
        embed.add_field(name="Backup Files", value="📁 No backups yet", inline=True)
    
    await ctx.send(embed=embed)

@commands.command()
async def topstreamers(ctx):
    """Show top 5 streamers by total stream time"""
    top_streamers = ctx.bot.database.get_top_streamers(5)

    embed = discord.Embed(
        title="🎬 Top 5 Streamers",
        description="Most dedicated streamers by total stream time",
        color=0x9146FF,
        timestamp=datetime.now()
    )

    if top_streamers:
        for i, streamer in enumerate(top_streamers, 1):
            hours = streamer['total_stream_time'] / 3600
            sessions = streamer['sessions']

            # Try to fetch current username from Discord
            user = ctx.bot.get_user(streamer['user_id'])
            if user:
                username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
            else:
                try:
                    user = await ctx.bot.fetch_user(streamer['user_id'])
                    username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
                except Exception:
                    username = "Unknown User"

            embed.add_field(
                name=f"{i}. {username}",
                value=f"⏱️ {hours:.1f} hours • {sessions} streams",
                inline=False
            )
    else:
        embed.description = "No streaming data yet! Start streaming to see stats."

    await ctx.send(embed=embed)

@commands.command()
async def topvoice(ctx):
    """Show top 5 voice channel users by time spent"""
    top_voice_users = ctx.bot.database.get_top_voice_users(5)

    embed = discord.Embed(
        title="🎧 Top 5 Voice Champions",
        description="Most active users in voice channels",
        color=0x00ff00,
        timestamp=datetime.now()
    )

    if top_voice_users:
        for i, user_data in enumerate(top_voice_users, 1):
            hours = user_data['total_voice_time'] / 3600
            sessions = user_data['sessions']

            # Try to fetch current username from Discord
            user = ctx.bot.get_user(user_data['user_id'])
            if user:
                username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
            else:
                try:
                    user = await ctx.bot.fetch_user(user_data['user_id'])
                    username = getattr(user, "display_name", getattr(user, "name", "Unknown"))
                except Exception:
                    username = "Unknown User"

            embed.add_field(
                name=f"{i}. {username}",
                value=f"⏱️ {hours:.1f} hours • {sessions} sessions",
                inline=False
            )
    else:
        embed.description = "No voice channel data yet! Join voice channels to see stats."

    await ctx.send(embed=embed)

@commands.command()
async def mystats(ctx):
    """Show user's personal statistics"""
    user_id = ctx.author.id

    # Get user's streaming stats
    top_streamers = ctx.bot.database.get_top_streamers(100)
    user_stream_rank = None
    user_stream_stats = None

    for i, streamer in enumerate(top_streamers, 1):
        if streamer['user_id'] == user_id:
            user_stream_rank = i
            user_stream_stats = streamer
            break

    # Get user's voice stats
    top_voice_users = ctx.bot.database.get_top_voice_users(100)
    user_voice_rank = None
    user_voice_stats = None

    for i, user_data in enumerate(top_voice_users, 1):
        if user_data['user_id'] == user_id:
            user_voice_rank = i
            user_voice_stats = user_data
            break

    embed = discord.Embed(
        title=f"📊 {ctx.author.display_name}'s Statistics",
        color=0x7289DA,
        timestamp=datetime.now()
    )

    # Streaming stats
    if user_stream_stats:
        stream_hours = user_stream_stats['total_stream_time'] / 3600
        embed.add_field(
            name="🎬 Streaming",
            value=f"**{stream_hours:.1f} hours**\n#{user_stream_rank} • {user_stream_stats['sessions']} streams",
            inline=True
        )
    else:
        embed.add_field(
            name="🎬 Streaming",
            value="No streaming data",
            inline=True
        )

    # Voice stats
    if user_voice_stats:
        voice_hours = user_voice_stats['total_voice_time'] / 3600
        embed.add_field(
            name="🎧 Voice Time",
            value=f"**{voice_hours:.1f} hours**\n#{user_voice_rank} • {user_voice_stats['sessions']} sessions",
            inline=True
        )
    else:
        embed.add_field(
            name="🎧 Voice Time",
            value="No voice data",
            inline=True
        )

    await ctx.send(embed=embed)

@commands.command()
async def debug(ctx):
    """Debug command to check database"""
    user_id = ctx.author.id

    top_voice = ctx.bot.database.get_top_voice_users(10)
    top_stream = ctx.bot.database.get_top_streamers(10)

    user_in_voice = any(user['user_id'] == user_id for user in top_voice)
    user_in_stream = any(user['user_id'] == user_id for user in top_stream)

    debug_info = f"**Debug Info for {ctx.author.display_name}**\n"
    debug_info += f"User ID: {user_id}\n"
    debug_info += f"Voice Users in DB: {len(top_voice)}\n"
    debug_info += f"Streamers in DB: {len(top_stream)}\n"
    debug_info += f"You in Voice Data: {user_in_voice}\n"
    debug_info += f"You in Stream Data: {user_in_stream}\n"

    await ctx.send(f"```{debug_info}```")


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
