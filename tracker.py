# tracker.py
from datetime import datetime

class VoiceTimeTracker:
    def __init__(self, database):
        self.db = database
        print("✅ VoiceTimeTracker initialized")

    async def handle_voice_state_update(self, member, before, after):
        """Track voice channel joins/leaves and streaming"""
        print(f"🎧 VOICE EVENT: {member.display_name} | "
              f"Before: {before.channel.name if before and before.channel else 'None'} | "
              f"After: {after.channel.name if after and after.channel else 'None'}")

        # User changed channel (join / leave)
        if (not before or not before.channel) and after and after.channel:
            # joined a channel
            print(f"✅ JOIN: {member.display_name} joined {after.channel.name}")
            await self.user_joined_voice(member, after.channel)
        elif before and before.channel and (not after or not after.channel):
            # left a channel
            print(f"✅ LEAVE: {member.display_name} left {before.channel.name}")
            await self.user_left_voice(member, before.channel)
        elif before and after and before.channel != after.channel:
            # moved channels
            print(f"🔀 MOVED: {member.display_name} moved from {before.channel.name} to {after.channel.name}")
            await self.user_left_voice(member, before.channel)
            await self.user_joined_voice(member, after.channel)

        # streaming changes
        if before and after:
            if not getattr(before, "self_stream", False) and getattr(after, "self_stream", False):
                print(f"🎬 STREAM START: {member.display_name}")
                await self.user_started_streaming(member, after.channel)
            if getattr(before, "self_stream", False) and not getattr(after, "self_stream", False):
                print(f"⏹️ STREAM STOP: {member.display_name}")
                await self.user_stopped_streaming(member, before.channel)

    async def user_joined_voice(self, member, channel):
        """User joined any voice channel"""
        print(f"📝 Starting voice session for {member.display_name}")
        self.db.start_voice_session(member.id, getattr(member, "display_name", getattr(member, "name", f"User_{member.id}")), channel.id)

    async def user_left_voice(self, member, channel):
        """User left any voice channel"""
        print(f"📝 Ending voice session for {member.display_name}")
        duration = self.db.end_voice_session(member.id)
        print(f"⏱️ Recorded {duration:.1f} minutes for {member.display_name}")

    async def user_started_streaming(self, member, channel):
        """User started screen sharing/streaming"""
        print(f"📝 Starting stream session for {member.display_name}")
        self.db.start_stream_session(member.id, getattr(member, "display_name", getattr(member, "name", f"User_{member.id}")), channel.id)

    async def user_stopped_streaming(self, member, channel):
        """User stopped screen sharing/streaming"""
        print(f"📝 Ending stream session for {member.display_name}")
        duration = self.db.end_stream_session(member.id)
        print(f"⏱️ Recorded {duration:.1f} streaming minutes for {member.display_name}")
