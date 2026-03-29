import asyncio
import os
import random
from collections import deque
import discord
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

intents = discord.Intents.default()
intents.presences = True
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(name="roll", description="주사위를 굴립니다. 1 ~ {숫자} 범위에서 랜덤한 숫자가 나옵니다.")
@app_commands.describe(sides="주사위의 최댓값 (1 이상의 정수)")
async def roll(interaction: discord.Interaction, sides: int):
    if sides < 1:
        await interaction.response.send_message("1 이상의 숫자를 입력해주세요.", ephemeral=True)
        return

    result = random.randint(1, sides)
    await interaction.response.send_message(f"🎲 **{result}**")


queue = deque()
current_track = None  # (audio_url, title)

# 가위바위보 게임 상태
rps_game = None  # {"players_needed": N, "choices": {user_id: (display_name, choice)}}

WINS_AGAINST = {"가위": "보", "바위": "가위", "보": "바위"}


def rps_result(choices: dict):
    """choices: {user_id: (display_name, choice)} -> 결과 문자열 반환"""
    unique = set(c for _, c in choices.values())
    lines = [f"**{name}**: {choice}" for name, choice in choices.values()]

    if len(unique) != 2:  # 모두 같거나 셋 다 있으면 무승부
        return "\n".join(lines) + "\n\n무승부!"

    # 두 종류 → 이긴 선택 찾기
    a, b = list(unique)
    winner_choice = a if WINS_AGAINST[a] == b else b
    winners = [name for name, choice in choices.values() if choice == winner_choice]
    losers = [name for name, choice in choices.values() if choice != winner_choice]

    return (
        "\n".join(lines)
        + f"\n\n🏆 승리: {', '.join(winners)}"
        + f"\n💀 패배: {', '.join(losers)}"
    )


@tree.command(name="rps_start", description="가위바위보 게임을 시작합니다.")
@app_commands.describe(players="참여 인원 수 (2~5명)")
async def rps_start(interaction: discord.Interaction, players: int):
    global rps_game
    if players < 2 or players > 5:
        await interaction.response.send_message("2명 이상 5명 이하로 입력해주세요.", ephemeral=True)
        return
    if rps_game is not None:
        await interaction.response.send_message("이미 진행 중인 게임이 있습니다. 먼저 끝내주세요.", ephemeral=True)
        return
    rps_game = {"players_needed": players, "choices": {}}
    await interaction.response.send_message(f"✂️ 가위바위보 시작! {players}명이 `/rps 가위/바위/보`를 입력해주세요.")


@tree.command(name="rps", description="가위바위보에서 선택합니다.")
@app_commands.describe(choice="가위, 바위, 보 중 하나")
@app_commands.choices(choice=[
    app_commands.Choice(name="가위", value="가위"),
    app_commands.Choice(name="바위", value="바위"),
    app_commands.Choice(name="보", value="보"),
])
async def rps(interaction: discord.Interaction, choice: str):
    global rps_game
    if rps_game is None:
        await interaction.response.send_message("진행 중인 게임이 없습니다. `/rps_start`로 시작해주세요.", ephemeral=True)
        return
    user_id = interaction.user.id
    if user_id in rps_game["choices"]:
        prev = rps_game["choices"][user_id][1]
        rps_game["choices"][user_id] = (interaction.user.display_name, choice)
        await interaction.response.send_message(f"🔄 선택 변경: {prev} → {choice}", ephemeral=True)
        return
    if len(rps_game["choices"]) >= rps_game["players_needed"]:
        await interaction.response.send_message("이미 인원이 다 찼습니다.", ephemeral=True)
        return
    rps_game["choices"][user_id] = (interaction.user.display_name, choice)
    remaining = rps_game["players_needed"] - len(rps_game["choices"])
    if remaining > 0:
        await interaction.response.send_message(f"✅ 선택 완료! 아직 {remaining}명 남았습니다.", ephemeral=True)
        return
    result = rps_result(rps_game["choices"])
    rps_game = None
    await interaction.response.send_message(f"✂️ **가위바위보 결과**\n\n{result}")


@tree.command(name="rps_stop", description="진행 중인 가위바위보 게임을 취소합니다.")
async def rps_stop(interaction: discord.Interaction):
    global rps_game
    if rps_game is None:
        await interaction.response.send_message("진행 중인 게임이 없습니다.", ephemeral=True)
        return
    rps_game = None
    await interaction.response.send_message("🛑 가위바위보 게임을 취소했습니다.")
loop = False

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def play_next(voice_client, error=None):
    if error:
        print(f"재생 오류: {error}")
    asyncio.run_coroutine_threadsafe(_play_next(voice_client), client.loop)


async def _play_next(voice_client):
    if not voice_client.is_connected():
        return
    global current_track, loop
    if loop and current_track:
        query, _, title = current_track
        new_url = await _fetch_url(query)
        if new_url:
            current_track = (query, new_url, title)
            voice_client.play(
                discord.FFmpegPCMAudio(new_url, **FFMPEG_OPTIONS),
                after=lambda e: play_next(voice_client, e)
            )
        else:
            print(f"반복 재생 URL 갱신 실패: {title}")
            current_track = None
    elif queue:
        query, audio_url, title = queue.popleft()
        current_track = (query, audio_url, title)
        voice_client.play(
            discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS),
            after=lambda e: play_next(voice_client, e)
        )
    else:
        current_track = None


async def _fetch_url(query: str):
    """반복 재생용 URL 재추출. 실패 시 None 반환."""
    is_url = query.startswith("http://") or query.startswith("https://")
    search_query = query if is_url else f"ytsearch:{query}"

    def extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not is_url:
                info = info["entries"][0]
            return info["url"]

    try:
        return await asyncio.get_running_loop().run_in_executor(None, extract)
    except (yt_dlp.utils.DownloadError, IndexError, KeyError):
        return None


async def fetch_track(interaction: discord.Interaction, query: str):
    """URL 또는 검색어로 오디오 정보를 추출. 실패 시 None 반환 후 에러 메시지 전송."""
    is_url = query.startswith("http://") or query.startswith("https://")
    search_query = query if is_url else f"ytsearch:{query}"

    def extract():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not is_url:
                info = info["entries"][0]
            return info["url"], info.get("title", "알 수 없는 제목")

    try:
        audio_url, title = await asyncio.get_event_loop().run_in_executor(None, extract)
        return query, audio_url, title
    except (yt_dlp.utils.DownloadError, IndexError, KeyError):
        await interaction.followup.send("영상을 찾을 수 없습니다.", ephemeral=True)
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected() and not voice_client.is_playing():
            await voice_client.disconnect()
        return None


async def join_voice(interaction: discord.Interaction):
    """음성 채널에 입장하거나 이동. voice_client 반환."""
    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.move_to(voice_channel)
    else:
        voice_client = await voice_channel.connect()
    return voice_client


# ── 음악 명령어 헬퍼 ──────────────────────────────────────────────────────────

async def _play_impl(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("먼저 음성 채널에 입장해주세요.", ephemeral=True)
        return

    await interaction.response.defer()
    voice_client = await join_voice(interaction)
    result = await fetch_track(interaction, url)
    if result is None:
        return
    query, audio_url, title = result

    if not voice_client.is_connected():
        await interaction.followup.send("음성 채널 연결이 끊겼습니다.", ephemeral=True)
        return

    if voice_client.is_playing():
        queue.append((query, audio_url, title))
        await interaction.followup.send(f"📋 대기열 추가: **{title}** (현재 대기 {len(queue)}곡)")
    else:
        global current_track
        current_track = (query, audio_url, title)
        voice_client.play(
            discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS),
            after=lambda e: play_next(voice_client, e)
        )
        await interaction.followup.send(f"▶️ **{title}**")


async def _playnext_impl(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("먼저 음성 채널에 입장해주세요.", ephemeral=True)
        return

    await interaction.response.defer()
    voice_client = await join_voice(interaction)
    result = await fetch_track(interaction, url)
    if result is None:
        return
    query, audio_url, title = result

    if not voice_client.is_connected():
        await interaction.followup.send("음성 채널 연결이 끊겼습니다.", ephemeral=True)
        return

    if voice_client.is_playing():
        queue.appendleft((query, audio_url, title))
        await interaction.followup.send(f"⏭️ 다음 곡으로 예약: **{title}**")
    else:
        global current_track
        current_track = (query, audio_url, title)
        voice_client.play(
            discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS),
            after=lambda e: play_next(voice_client, e)
        )
        await interaction.followup.send(f"▶️ **{title}**")


async def _playing_impl(interaction: discord.Interaction):
    if not current_track:
        await interaction.response.send_message("현재 재생 중인 곡이 없습니다.", ephemeral=True)
        return
    await interaction.response.send_message(f"▶️ **{current_track[2]}**")


async def _pause_impl(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message("현재 재생 중인 곡이 없습니다.", ephemeral=True)
        return
    voice_client.pause()
    await interaction.response.send_message("⏸️ 일시정지했습니다.")


async def _resume_impl(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_paused():
        await interaction.response.send_message("일시정지된 곡이 없습니다.", ephemeral=True)
        return
    voice_client.resume()
    await interaction.response.send_message("▶️ 재생을 재개합니다.")


async def _queue_impl(interaction: discord.Interaction):
    if not queue:
        await interaction.response.send_message("대기열이 비어있습니다.", ephemeral=True)
        return
    lines = [f"{i+1}. **{title}**" for i, (_, __, title) in enumerate(queue)]
    await interaction.response.send_message("📋 **대기열**\n" + "\n".join(lines))


async def _remove_impl(interaction: discord.Interaction, index: int):
    if index < 1 or index > len(queue):
        await interaction.response.send_message("올바른 번호를 입력해주세요.", ephemeral=True)
        return
    queue_list = list(queue)
    removed = queue_list.pop(index - 1)
    queue.clear()
    queue.extend(queue_list)
    await interaction.response.send_message(f"🗑️ 제거했습니다: **{removed[2]}**")


async def _shuffle_impl(interaction: discord.Interaction):
    if len(queue) < 2:
        await interaction.response.send_message("대기열에 곡이 2개 이상 있어야 합니다.", ephemeral=True)
        return
    queue_list = list(queue)
    random.shuffle(queue_list)
    queue.clear()
    queue.extend(queue_list)
    lines = [f"{i+1}. **{title}**" for i, (_, __, title) in enumerate(queue)]
    await interaction.response.send_message("🔀 **대기열을 섞었습니다.**\n" + "\n".join(lines))


async def _loop_impl(interaction: discord.Interaction):
    global loop
    loop = not loop
    state = "켜졌습니다 🔁" if loop else "꺼졌습니다"
    await interaction.response.send_message(f"반복 재생이 {state}")


async def _skip_impl(interaction: discord.Interaction):
    global loop
    voice_client = interaction.guild.voice_client
    if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
        await interaction.response.send_message("현재 재생 중인 곡이 없습니다.", ephemeral=True)
        return
    loop = False
    voice_client.stop()  # after 콜백이 play_next를 호출함
    await interaction.response.send_message("⏭️ 건너뛰었습니다.")


async def _stop_impl(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        global current_track, loop
        queue.clear()
        current_track = None
        loop = False
        voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ 재생을 멈추고 채널에서 나왔습니다.")
    else:
        await interaction.response.send_message("봇이 음성 채널에 없습니다.", ephemeral=True)


# ── 음악 명령어 (영어 / 한국어) ───────────────────────────────────────────────

@tree.command(name="play", description="유튜브 링크 또는 검색어로 오디오를 재생합니다.")
@app_commands.describe(url="유튜브 링크 또는 검색어")
async def play(interaction: discord.Interaction, url: str):
    await _play_impl(interaction, url)


@tree.command(name="재생", description="유튜브 링크 또는 검색어로 오디오를 재생합니다.")
@app_commands.describe(url="유튜브 링크 또는 검색어")
async def play_ko(interaction: discord.Interaction, url: str):
    await _play_impl(interaction, url)


@tree.command(name="playnext", description="다음 곡으로 대기열 맨 앞에 추가합니다.")
@app_commands.describe(url="유튜브 링크 또는 검색어")
async def playnext(interaction: discord.Interaction, url: str):
    await _playnext_impl(interaction, url)


@tree.command(name="다음재생", description="다음 곡으로 대기열 맨 앞에 추가합니다.")
@app_commands.describe(url="유튜브 링크 또는 검색어")
async def playnext_ko(interaction: discord.Interaction, url: str):
    await _playnext_impl(interaction, url)


@tree.command(name="playing", description="현재 재생 중인 곡을 확인합니다.")
async def playing(interaction: discord.Interaction):
    await _playing_impl(interaction)


@tree.command(name="현재곡", description="현재 재생 중인 곡을 확인합니다.")
async def playing_ko(interaction: discord.Interaction):
    await _playing_impl(interaction)


@tree.command(name="pause", description="재생을 일시정지합니다.")
async def pause(interaction: discord.Interaction):
    await _pause_impl(interaction)


@tree.command(name="일시정지", description="재생을 일시정지합니다.")
async def pause_ko(interaction: discord.Interaction):
    await _pause_impl(interaction)


@tree.command(name="resume", description="일시정지된 재생을 재개합니다.")
async def resume(interaction: discord.Interaction):
    await _resume_impl(interaction)


@tree.command(name="계속재생", description="일시정지된 재생을 재개합니다.")
async def resume_ko(interaction: discord.Interaction):
    await _resume_impl(interaction)


@tree.command(name="queue", description="현재 대기열을 확인합니다.")
async def show_queue(interaction: discord.Interaction):
    await _queue_impl(interaction)


@tree.command(name="대기열", description="현재 대기열을 확인합니다.")
async def show_queue_ko(interaction: discord.Interaction):
    await _queue_impl(interaction)


@tree.command(name="remove", description="대기열에서 특정 번호의 곡을 제거합니다.")
@app_commands.describe(index="제거할 곡 번호")
async def remove(interaction: discord.Interaction, index: int):
    await _remove_impl(interaction, index)


@tree.command(name="제거", description="대기열에서 특정 번호의 곡을 제거합니다.")
@app_commands.describe(index="제거할 곡 번호")
async def remove_ko(interaction: discord.Interaction, index: int):
    await _remove_impl(interaction, index)


@tree.command(name="shuffle", description="대기열을 섞습니다.")
async def shuffle(interaction: discord.Interaction):
    await _shuffle_impl(interaction)


@tree.command(name="셔플", description="대기열을 섞습니다.")
async def shuffle_ko(interaction: discord.Interaction):
    await _shuffle_impl(interaction)


@tree.command(name="loop", description="현재 곡 반복 재생을 켜거나 끕니다.")
async def loop_toggle(interaction: discord.Interaction):
    await _loop_impl(interaction)


@tree.command(name="반복", description="현재 곡 반복 재생을 켜거나 끕니다.")
async def loop_toggle_ko(interaction: discord.Interaction):
    await _loop_impl(interaction)


@tree.command(name="skip", description="현재 곡을 건너뛰고 다음 곡을 재생합니다.")
async def skip(interaction: discord.Interaction):
    await _skip_impl(interaction)


@tree.command(name="건너뛰기", description="현재 곡을 건너뛰고 다음 곡을 재생합니다.")
async def skip_ko(interaction: discord.Interaction):
    await _skip_impl(interaction)


@tree.command(name="stop", description="재생을 멈추고 음성 채널에서 나갑니다.")
async def stop(interaction: discord.Interaction):
    await _stop_impl(interaction)


@tree.command(name="정지", description="재생을 멈추고 음성 채널에서 나갑니다.")
async def stop_ko(interaction: discord.Interaction):
    await _stop_impl(interaction)


@tree.command(name="help", description="사용 가능한 명령어 목록을 확인합니다.")
async def help_command(interaction: discord.Interaction):
    msg = (
        "**📋 명령어 목록**\n\n"
        "**[[ 🎲 미니게임 ]]**\n\n"
        "🎲 주사위 굴리기\n"
        "`/roll {숫자}` — 1 ~ 숫자 범위에서 랜덤 숫자\n\n"
        "✂️  가위바위보\n"
        "`/rps_start {인원수}` — 가위바위보 시작 (2~5명)\n"
        "`/rps {가위/바위/보}` — 선택 (변경 가능)\n"
        "`/rps_stop` — 진행 중인 게임 취소\n\n"
        "**[[ 🎵 음악 ]]**\n\n"
        "▶️  재생 / 정지\n"
        "`/play` `/재생` — 음악 재생 또는 대기열 추가\n"
        "`/pause` `/일시정지` — 일시정지\n"
        "`/resume` `/계속재생` — 재개\n"
        "`/skip` `/건너뛰기` — 현재 곡 건너뛰기\n"
        "`/stop` `/정지` — 재생 중지 및 채널 퇴장\n"
        "`/loop` `/반복` — 현재 곡 반복 재생 토글\n"
        "`/playing` `/현재곡` — 현재 재생 중인 곡 확인\n\n"
        "📋 대기열\n"
        "`/playnext` `/다음재생` — 대기열 맨 앞에 추가\n"
        "`/queue` `/대기열` — 대기열 확인\n"
        "`/remove` `/제거` — 특정 곡 제거\n"
        "`/shuffle` `/셔플` — 대기열 섞기\n"
    )
    await interaction.response.send_message(msg)


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, _after: discord.VoiceState):
    voice_client = member.guild.voice_client
    if not voice_client:
        return
    # 봇 자신의 상태 변경은 무시
    if member == member.guild.me:
        return
    # 봇이 있는 채널에서 사람이 나갔을 때 확인
    if before.channel == voice_client.channel:
        non_bot_members = [m for m in voice_client.channel.members if not m.bot]
        if not non_bot_members:
            global current_track, loop
            queue.clear()
            current_track = None
            loop = False
            voice_client.stop()
            await voice_client.disconnect()


@client.event
async def on_ready():
    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"길드 동기화 완료 (ID: {guild_id})")
    else:
        await tree.sync()
        print("전역 동기화 완료 (최대 1시간 소요)")
    print(f"봇 로그인: {client.user} (ID: {client.user.id})")


client.run(os.getenv("DISCORD_TOKEN"))
