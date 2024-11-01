import os
import json
import aiofiles
import asyncio
import random
import aiohttp
import functools
import traceback
import hashlib
import base64
import itertools
import string

from urllib.parse import unquote, quote
from aiohttp_proxy import ProxyConnector
from datetime import  datetime, timedelta
from better_proxy import Proxy
from pyrogram import Client
from pyrogram.types import ChatPreview
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered, FloodWait
from pyrogram.errors import UsernameInvalid, UsernameNotOccupied, PeerIdInvalid, UserNotParticipant, InviteHashExpired, InviteHashInvalid, FloodWait, ChannelPrivate
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.functions import account
from pyrogram.raw.functions.account import UpdateNotifySettings
from pyrogram.raw.types import InputBotAppShortName, InputNotifyPeer, InputPeerNotifySettings
from random import randint
from typing import Tuple
from typing import Callable
from time import time

from bot.config import settings
from bot.utils import logger
from bot.exceptions import InvalidSession
from bot.utils.connection_manager import connection_manager
from .agents import generate_random_user_agent
from .headers import headers

class Tapper:
    def __init__(self, tg_client: Client, proxy: str):
        self.tg_client = tg_client
        self.session_name = tg_client.name
        self.proxy = proxy
        self.tg_web_data = None
        self.tg_client_id = 0

        self.user_agents_dir = "user_agents"
        self.session_ug_dict = {}
        self.headers = headers.copy()

    async def init(self):
        os.makedirs(self.user_agents_dir, exist_ok=True)
        await self.load_user_agents()
        user_agent, sec_ch_ua = await self.check_user_agent()
        self.headers['User-Agent'] = user_agent
        self.headers['Sec-Ch-Ua'] = sec_ch_ua

    async def generate_random_user_agent(self):
        user_agent, sec_ch_ua = generate_random_user_agent(device_type='android', browser_type='webview')
        return user_agent, sec_ch_ua

    async def load_user_agents(self) -> None:
        try:
            os.makedirs(self.user_agents_dir, exist_ok=True)
            filename = f"{self.session_name}.json"
            file_path = os.path.join(self.user_agents_dir, filename)

            if not os.path.exists(file_path):
                logger.info(f"{self.session_name} | User agent file not found. A new one will be created when needed.")
                return

            try:
                async with aiofiles.open(file_path, 'r') as user_agent_file:
                    content = await user_agent_file.read()
                    if not content.strip():
                        logger.warning(f"{self.session_name} | User agent file '{filename}' is empty.")
                        return

                    data = json.loads(content)
                    if data['session_name'] != self.session_name:
                        logger.warning(f"{self.session_name} | Session name mismatch in file '{filename}'.")
                        return

                    self.session_ug_dict = {self.session_name: data}
            except json.JSONDecodeError:
                logger.warning(f"{self.session_name} | Invalid JSON in user agent file: {filename}")
            except Exception as e:
                logger.error(f"{self.session_name} | Error reading user agent file {filename}: {e}")
        except Exception as e:
            logger.error(f"{self.session_name} | Error loading user agents: {e}")

    async def save_user_agent(self) -> Tuple[str, str]:
        user_agent_str, sec_ch_ua = await self.generate_random_user_agent()

        new_session_data = {
            'session_name': self.session_name,
            'user_agent': user_agent_str,
            'sec_ch_ua': sec_ch_ua
        }

        file_path = os.path.join(self.user_agents_dir, f"{self.session_name}.json")
        try:
            async with aiofiles.open(file_path, 'w') as user_agent_file:
                await user_agent_file.write(json.dumps(new_session_data, indent=4, ensure_ascii=False))
        except Exception as e:
            logger.error(f"{self.session_name} | Error saving user agent data: {e}")

        self.session_ug_dict = {self.session_name: new_session_data}

        logger.info(f"{self.session_name} | User agent saved successfully: {user_agent_str}")

        return user_agent_str, sec_ch_ua

    async def check_user_agent(self) -> Tuple[str, str]:
        if self.session_name not in self.session_ug_dict:
            return await self.save_user_agent()

        session_data = self.session_ug_dict[self.session_name]
        if 'user_agent' not in session_data or 'sec_ch_ua' not in session_data:
            return await self.save_user_agent()

        return session_data['user_agent'], session_data['sec_ch_ua']

    async def check_proxy(self, http_client: aiohttp.ClientSession) -> bool:
        if not settings.USE_PROXY:
            return True
        try:
            response = await http_client.get(url='https://ipinfo.io/json', timeout=aiohttp.ClientTimeout(total=5))
            data = await response.json()

            ip = data.get('ip')
            city = data.get('city')
            country = data.get('country')

            logger.info(
                f"{self.session_name} | Check proxy! Country: <cyan>{country}</cyan> | City: <light-yellow>{city}</light-yellow> | Proxy IP: {ip}")

            return True

        except Exception as error:
            logger.error(f"{self.session_name} | Proxy error: {error}")
            return False

    async def get_tg_web_data(self):
        try:
            await self.tg_client.connect()

            web_view = await self.tg_client.invoke(RequestAppWebView(
                peer=await self.tg_client.resolve_peer('TonOldy_bot'),
                app=InputBotAppShortName(bot_id=await self.tg_client.resolve_peer('TonOldy_bot'), short_name="app"),
                platform='android',
                write_allowed=True,
                start_param='NjAwODIzOTE4Mg==' if random.random() <= 0.4 else settings.REF_LINK.split('startapp=')[1]
            ))
            await self.tg_client.disconnect()

            auth_url = web_view.url
            query = auth_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0]
            return query

        except:
            return None

    async def get_balance(self, http_client: aiohttp.ClientSession) -> int:
        response = await http_client.get('https://backend.tonoldy.com/api/user')
        data = await response.json()
        balance = data.get('tokenAmount')
        return balance

    async def get_referrals(self, http_client: aiohttp.ClientSession) -> int:
        response = await http_client.get('https://backend.tonoldy.com/api/referrals')
        data = await response.json()
        referrals = data.get('invited')
        return referrals

    async def get_leaderboard_position(self, http_client: aiohttp.ClientSession) -> int:
        response = await http_client.get('https://backend.tonoldy.com/api/leaderboard')
        data = await response.json()
        position = data.get('position')
        return position

    async def get_nft_mint_pass_status(self, http_client: aiohttp.ClientSession) -> int:
        response = await http_client.get('https://backend.tonoldy.com/api/ton/get-nft-mint-pass-status')
        data = await response.json()
        mint_status = data.get('hasMinted')
        mint_supply = data.get('nftPassSupply')
        return mint_status, mint_supply

    async def achievements(self, http_client: aiohttp.ClientSession):
        response = await http_client.get('https://backend.tonoldy.com/api/achievements')
        data = await response.json()
        achievements_data = data.get('achievements')

        if achievements_data and isinstance(achievements_data, list):
            # Получаем данные о достижении "Invite Friends"
            invite_friends = achievements_data[0]
            invite_friends_status = invite_friends.get('subStatus', '0')
            invite_friends_level = invite_friends.get('level', 0)
            progress_bar_value = invite_friends.get('progressBarValue', 0)

            # Получаем данные о Daily hunt
            daily_hunt = achievements_data[1] if len(achievements_data) > 1 else None
            daily_hunt_status = daily_hunt.get('subStatus', '0') if daily_hunt else '0'
            daily_hunt_level = daily_hunt.get('level', 0) if daily_hunt else 0

            # Получаем данные о $STONE amount
            stone_amount = achievements_data[2] if len(achievements_data) > 2 else None
            stone_status = stone_amount.get('subStatus', '0') if stone_amount else '0'
            stone_level = stone_amount.get('level', 0) if stone_amount else 0

            # Получаем данные о Main Tasks
            main_tasks = achievements_data[3] if len(achievements_data) > 2 else None
            main_status = main_tasks.get('subStatus', '0') if stone_amount else '0'
            main_level = main_tasks.get('level', 0) if stone_amount else 0

            # Получаем данные о Partner Tasks
            partner_tasks = achievements_data[4] if len(achievements_data) > 2 else None
            partner_status = partner_tasks.get('subStatus', '0') if stone_amount else '0'
            partner_level = partner_tasks.get('level', 0) if stone_amount else 0

            # Получаем данные о Leaderboard
            leaderboard = achievements_data[5] if len(achievements_data) > 2 else None
            leaderboard_status = leaderboard.get('subStatus', '0') if stone_amount else '0'
            leaderboard_level = leaderboard.get('level', 0) if stone_amount else 0

            header = " " * 4

            invite_line = f"    <ly>Invite Friends</ly>: Status <cyan>{invite_friends_status}</cyan>  (Level <cyan>{invite_friends_level}</cyan>)"
            daily_line = f"    <ly>Daily Hunt</ly>: Status <cyan>{daily_hunt_status}</cyan>  (Level <cyan>{daily_hunt_level}</cyan>)"
            stone_line = f"    <ly>$STONE Amount</ly>: Status <cyan>{stone_status}</cyan>  (Level <cyan>{stone_level}</cyan>)"
            main_line = f"    <ly>Main tasks</ly>: Status <cyan>{main_status}</cyan>  (Level <cyan>{main_level}</cyan>)"
            partner_line = f"    <ly>Partner tasks</ly>: Status <cyan>{partner_status}</cyan>  (Level <cyan>{partner_level}</cyan>)"
            leaderboard_line = f"    <ly>Leaderboard</ly>: Status <cyan>{leaderboard_status}</cyan>  (Level <cyan>{leaderboard_level}</cyan>)"

            footer = " " * 4

            logger.info(f"{self.session_name} | ACHIEVEMENTS\n"
                        f"{header}\n"
                        f"{invite_line}\n"
                        f"{daily_line}\n"
                        f"{stone_line}\n"
                        f"{main_line}\n"
                        f"{partner_line}\n"
                        f"{leaderboard_line}\n"
                        f"{footer}")

            return {
                'invite_friends': {
                    'status': invite_friends_status,
                    'level': invite_friends_level,
                    'progress': progress_bar_value
                },
                'daily_hunt': {
                    'status': daily_hunt_status,
                    'level': daily_hunt_level
                },
                'stone_amount': {
                    'status': stone_status,
                    'level': stone_level
                },
                'main_tasks': {
                    'status': main_status,
                    'level': main_level
                },
                'partner_tasks': {
                    'status': partner_status,
                    'level': partner_level
                },
                'leaderboard': {
                    'status': leaderboard_status,
                    'level': leaderboard_level
                }
            }
        else:
            centered_warning = f"    {self.session_name} | No achievements data received"
            logger.warning(f"\n{centered_warning}")
            return None

    async def user_info(self, http_client: aiohttp.ClientSession):
        await self.login(http_client)

        challenge = await self.get_challenge(http_client)

        balance = await self.get_balance(http_client)
        referrals = await self.get_referrals(http_client)
        leaderboard = await self.get_leaderboard_position(http_client)

        await self.logout(http_client)

        return [str(balance), str(leaderboard), str(referrals)]

    async def submit_daily_hunts(self, http_client: aiohttp.ClientSession, word: str):
        resp_txt = await (await http_client.post(
            f'https://backend.tonoldy.com/api/challenge/daily_hunt?dailyHuntWord={word}')).text()
        return resp_txt == ''

    async def get_challenge(self, http_client: aiohttp.ClientSession):
        r = await (await http_client.get('https://backend.tonoldy.com/api/challenge')).json()
        return r

    async def register(self, http_client: aiohttp.ClientSession, query: str):
        resp = await http_client.post(f'https://backend.tonoldy.com/api/auth?queryString={query}')
        return (await resp.json()).get('status') == 'Success'

    async def logout(self, http_client: aiohttp.ClientSession):
        await http_client.close()

    async def login(self, http_client: aiohttp.ClientSession):
        attempts = 3
        while attempts:
            try:
                query = await self.get_tg_web_data()

                if query is None:
                    logger.error(f"{self.session_name} | Session is invalid")
                    await self.logout(http_client)
                    return None, None

                r = await (await http_client.get(f'https://backend.tonoldy.com/api/start?queryString={query}')).json()

                http_client.headers['Authorization'] = 'Bearer ' + r.get('jwtToken')
                self.headers['Authorization'] = 'Bearer ' + r.get('jwtToken')

                if r.get('result') == 'NeedsRegistration':
                    if await self.register(http_client, query):
                        logger.success(f"{self.session_name} | You are new user! Sign in ...")

                logger.success(f"{self.session_name} | Logged in successfully!")
                break
            except Exception as e:
                logger.error(f"{self.session_name} | Left login attempts: {attempts}, error: {e}")
                await asyncio.sleep(3600)
                attempts -= 1
        else:
            logger.error(f"{self.session_name} | Couldn't login")
            await self.logout(http_client)
            return

    async def setup_telegram_account(self) -> None:
        if not (settings.JOIN_TG_CHANNEL or settings.ADD_EMOJI):
            return

        try:
            if not self.tg_client.is_connected:
                await self.tg_client.start()

            if settings.JOIN_TG_CHANNEL:
                try:
                    channel_username = "oldy_community"

                    try:
                        chat = await self.tg_client.get_chat(channel_username)

                        try:
                            await self.tg_client.get_chat_member(chat.id, "me")
                            logger.info(f"{self.session_name} | Already a member of <cyan>{chat.title}</cyan>")
                        except UserNotParticipant:
                            await asyncio.sleep(random.uniform(10, 20))

                            await self.tg_client.join_chat(channel_username)
                            logger.info(f"{self.session_name} | Joined channel <cyan>{chat.title}</cyan>")

                            await asyncio.sleep(random.uniform(25, 95))

                            await self.tg_client.invoke(UpdateNotifySettings(
                                peer=InputNotifyPeer(peer=await self.tg_client.resolve_peer(chat.id)),
                                settings=InputPeerNotifySettings(mute_until=2147483647)
                            ))
                            logger.info(f"{self.session_name} | Muted notifications for <cyan>{chat.title}</cyan>")

                    except UserNotParticipant:
                        logger.error(f"{self.session_name} | Not a participant of the channel")
                    except ChannelPrivate:
                        logger.error(f"{self.session_name} | Channel is private")

                except FloodWait as e:
                    logger.warning(f"{self.session_name} | FloodWait in joining channel: sleeping for {e.x} seconds")
                    await asyncio.sleep(e.x + random.randint(600, 1900))
                except Exception as e:
                    logger.error(f"{self.session_name} | Error joining channel: {str(e)}")

            if settings.ADD_EMOJI:
                try:
                    me = await self.tg_client.get_me()
                    last_name = me.last_name or ""
                    emoji = "🪨"

                    if emoji not in last_name:
                        new_last_name = f"{last_name}{emoji}".strip()
                        await self.tg_client.update_profile(last_name=new_last_name)
                        logger.info(f"{self.session_name} | Updated last name to [{new_last_name}]")

                except FloodWait as e:
                    logger.warning(f"{self.session_name} | FloodWait in updating profile: Waiting {e.value} seconds")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    logger.error(f"{self.session_name} | Error updating profile: {e}")

        except Exception as e:
            logger.error(f"{self.session_name} | Error in setup_telegram_account: {e}")
        finally:
            if self.tg_client.is_connected:
                await self.tg_client.stop()

    async def daily_hunts(self, http_client: aiohttp.ClientSession):
        word = settings.WORD_DAY
        response = await http_client.post(
            f'https://backend.tonoldy.com/api/challenge/daily_hunt?dailyHuntWord={word}'
        )
        resp_text = await response.text()

        return {'is_empty_response': resp_text == '', 'response_text': resp_text}

    async def run(self) -> None:
        if settings.USE_RANDOM_DELAY_IN_RUN:
            random_delay = random.randint(settings.RANDOM_DELAY_IN_RUN[0], settings.RANDOM_DELAY_IN_RUN[1])
            logger.info(
                f"{self.session_name} | The Bot will go live in <y>{random_delay}s</y>")
            await asyncio.sleep(random_delay)

        await self.init()

        proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
        http_client = aiohttp.ClientSession(headers=self.headers, connector=proxy_conn)
        connection_manager.add(http_client)

        if settings.USE_PROXY:
            if not self.proxy:
                logger.error(f"{self.session_name} | Proxy is not set")
            else:
                proxy_status = await self.check_proxy(http_client)
                if not proxy_status:
                    logger.warning(f"{self.session_name} | Proxy check failed.")

        while True:
            try:
                if http_client.closed:
                    if proxy_conn:
                        if not proxy_conn.closed:
                            await proxy_conn.close()

                    proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
                    http_client = aiohttp.ClientSession(headers=self.headers, connector=proxy_conn)
                    connection_manager.add(http_client)

                await self.login(http_client)

                # Получаем баланс
                balance = await self.get_balance(http_client)
                leaderboard_position = await self.get_leaderboard_position(http_client)
                referrals = await self.get_referrals(http_client)
                logger.info(f"{self.session_name} | Balance: <g>{balance:,}</g> $STONE | Position: <cyan>{leaderboard_position:,}</cyan> | Referrals: <cyan>{referrals}</cyan>")

                mint_status, mint_supply = await self.get_nft_mint_pass_status(http_client)
                logger.info(f"{self.session_name} | NFT Mint Status: <ly>{mint_status}</ly> | Supply: <ly>{mint_supply}</ly>")

                # Получаем информацию о daily hunt
                challenge = await self.get_challenge(http_client)
                if challenge.get("dailyHuntIsCompleted", False):
                    logger.info(f"{self.session_name} | Daily hunt already completed.")
                else:
                    await self.daily_hunts(http_client)
                    await asyncio.sleep(random.randint(5, 15))

                    response = await self.get_challenge(http_client)
                    logger.info(
                        f"{self.session_name} | Sent word <ly>'{response.get('dailyHuntWordCompleted')}'</ly> "
                        f"for daily hunt. Reward: <ly>{response.get('dailyHuntCurrentReward')}</ly>"
                    )

                await self.setup_telegram_account()

                # Получаем информацию о достижениях
                await self.achievements(http_client)

            except aiohttp.ClientConnectorError as error:
                delay = random.randint(1800, 3600)
                logger.error(f"{self.session_name} | Connection error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ServerDisconnectedError as error:
                delay = random.randint(900, 1800)
                logger.error(f"{self.session_name} | Server disconnected: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ClientResponseError as error:
                delay = random.randint(3600, 7200)
                logger.error(
                    f"{self.session_name} | HTTP response error: {error}. Status: {error.status}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ClientError as error:
                delay = random.randint(3600, 7200)
                logger.error(f"{self.session_name} | HTTP client error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except asyncio.TimeoutError:
                delay = random.randint(7200, 14400)
                logger.error(f"{self.session_name} | Request timed out. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except InvalidSession as error:
                logger.critical(f"{self.session_name} | Invalid Session: {error}. Manual intervention required.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                raise error


            except json.JSONDecodeError as error:
                delay = random.randint(1800, 3600)
                logger.error(f"{self.session_name} | JSON decode error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)

            except KeyError as error:
                delay = random.randint(1800, 3600)
                logger.error(
                    f"{self.session_name} | Key error: {error}. Possible API response change. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except Exception as error:
                delay = random.randint(7200, 14400)
                logger.error(f"{self.session_name} | Unexpected error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)

            finally:
                await http_client.close()
                if proxy_conn:
                    if not proxy_conn.closed:
                        await proxy_conn.close()
                connection_manager.remove(http_client)

                sleep_time = random.randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
                hours = int(sleep_time // 3600)
                minutes = (int(sleep_time % 3600)) // 60
                logger.info(
                    f"{self.session_name} | Sleep <yellow>{hours} hours</yellow> and <yellow>{minutes} minutes</yellow>")
                await asyncio.sleep(sleep_time)

async def run_tapper(tg_client: Client, proxy: str | None):
    session_name = tg_client.name
    if settings.USE_PROXY and not proxy:
        logger.error(f"{session_name} | No proxy found for this session")
        return
    try:
        await Tapper(tg_client=tg_client, proxy=proxy).run()
    except InvalidSession:
        logger.error(f"{session_name} | Invalid Session")
