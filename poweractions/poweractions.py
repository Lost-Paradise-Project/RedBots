import asyncio
import base64
import aiohttp
from typing import Any, Optional
from discord import Embed, app_commands
from redbot.core import commands, checks, Config
from red_commons.logging import getLogger
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils import menus
import discord
from redbot.core.utils.views import ConfirmView

log = getLogger("red.wizard-cogs.gameserverstatus")


# Input class for the discord modal
class Input(discord.ui.Modal, title='Введите данные сервера'):
    name = discord.ui.TextInput(label='Название', placeholder='Название сервера (Вы можете выбрать его сами)', required=True)
    url = discord.ui.TextInput(label='URL-адрес Watchdog',
                               placeholder='URL-адрес сервера Watchdog (https://ss14.io/watchdog http://localhost:5000)',
                               required=True)
    key = discord.ui.TextInput(label='ID сервера',
                               placeholder='ID сервера (ID самого сервера)',
                               required=True)
    token = discord.ui.TextInput(label='Токен API',
                                 placeholder='Токен сервера (Значение ApiToken)',
                                 required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Обработка...", ephemeral=True)
        self.stop()


# Button to bring up the modal
class Button(discord.ui.View):
    def __init__(self, member):
        self.member = member
        super().__init__()
        self.modal = None

    @discord.ui.button(label='Добавить', style=discord.ButtonStyle.green)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.member != interaction.user:
            return await interaction.response.send_message("Вы не можете это использовать.", ephemeral=True)

        self.modal = Input()
        await interaction.response.send_modal(self.modal)
        await self.modal.wait()
        self.stop()

ACTION_TIMEOUT = 5

async def doaction(session: aiohttp.ClientSession, server, action: str) -> tuple[int, str]:
    async def load() -> tuple[int, str]:
        async with session.post(server["address"] + f"/instances/{server['key']}/{action}",
                                auth=aiohttp.BasicAuth(server['key'], server['token'])) as resp:
            return resp.status, await resp.text()

    return await asyncio.wait_for(load(), timeout=ACTION_TIMEOUT)

class poweractions(commands.Cog):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = Config.get_conf(self, identifier=275978)

        default_guild = {
            "servers": {},
        }

        self.config.register_guild(**default_guild)

        self.bot = bot

    @commands.hybrid_group()
    @checks.admin()
    async def poweractionscfg(self, ctx: commands.Context) -> None:
        """
        Commands for configuring the servers to be able to manage the actions for power actions.
        """
        pass

    @poweractionscfg.command()
    async def add(self, ctx: commands.Context) -> None:
        """
        Adds a server.
        """
        view = Button(member=ctx.author)

        await ctx.send("Чтобы добавить сервер, нажмите эту кнопку.", view=view)
        await view.wait()
        if view.modal is None:
            return
        if not view.modal.name.value:
            return

        async with self.config.guild(ctx.guild).servers() as cur_servers:
            if view.modal.name.value in cur_servers:
                await ctx.send("Сервер с таким названием уже существует.")
                return

            if not view.modal.url.value.startswith("http://") and not view.modal.url.value.startswith("https://"):
                await ctx.send("URL-адрес должен начинаться с http:// или https://")
                return

            # Remove trailing slash at the end of the URL
            if view.modal.url.value.endswith("/"):
                await ctx.send("Удалите косую черту в конце URL-адреса.")

            if view.modal.url.value.endswith(f"/instances/{view.modal.key.value}/restart"):
                await ctx.send("Последняя часть URL-адреса не нужна, только базовый URL-адрес для watchdog (Пример: "
                               "https://ss14.io/watchdog, http://localhost:5000)")
                return

            cur_servers[view.modal.name.value] = {
                "address": view.modal.url.value,
                "key": view.modal.key.value,
                "token": view.modal.token.value
            }

        await ctx.send("Сервер успешно добавлен.")

    @poweractionscfg.command()
    async def remove(self, ctx: commands.Context, name: str) -> None:
        """
        Removes a server.

        `<name>`: The name of the server to remove.
        """
        async with self.config.guild(ctx.guild).servers() as cur_servers:
            if name not in cur_servers:
                await ctx.send("Этого сервера не существует.")
                return

            del cur_servers[name]

        await ctx.tick()

    @poweractionscfg.command()
    async def list(self, ctx: commands.Context) -> None:
        """
        Get a list of servers.
        """
        servers = await self.config.guild(ctx.guild).servers()

        if len(servers) == 0:
            await ctx.send("В настоящее время серверы не настроены!")
            return

        content = "\n".join(map(lambda s: f"{s[0]}: `{s[1]['address']}`", servers.items()))

        pages = list(pagify(content, page_length=1024))
        embed_pages = []
        for idx, page in enumerate(pages, start=1):
            embed = discord.Embed(
                title="Список серверов",
                description=page,
                colour=await ctx.embed_colour(),
            )
            embed.set_footer(text="Страница {num}/{total}".format(num=idx, total=len(pages)))
            embed_pages.append(embed)
        await menus.menu(ctx, embed_pages, menus.DEFAULT_CONTROLS)

    @checks.admin()
    @commands.hybrid_command()
    async def restartserver(self, ctx: commands.Context, server: Optional[str]) -> None:
        """
        Restarts a server.

        `<server>`: The name of the server to restart.
        """
        if not server:
            await self.list(ctx)
            return

        async with ctx.typing():
            foundServer = await self.get_server_from_arg(ctx, server)
            if foundServer is None:
                return
            
            servername, server = foundServer

            async with aiohttp.ClientSession() as session:
                try:
                    status, response = await doaction(session, server, "restart")
                    if status != 200:
                        await ctx.send(f"Не удалось перезапустить сервер. Неверный код состояния: {status}")
                        log.debug(f"Failed to restart {servername}. Wrong status code: {status} Response: {response}")
                        return

                except asyncio.TimeoutError:
                    await ctx.send("Время ожидания сервера истекло.")
                    return

                except Exception:
                    await ctx.send(
                        f"При попытке перезапустить этот сервер произошла неизвестная ошибка, информация регистрируется в консоли...")
                    log.exception(
                        f"An error occurred while trying restart server {servername}.")
                    return

            await ctx.send("Сервер успешно перезапущен.")

    @checks.admin()
    @commands.hybrid_command()
    async def updateserver(self, ctx: commands.Context, server: Optional[str]) -> None:
        """
        Sends an update request to a server.

        `<server>`: The name of the server to update.
        """
        if not server:
            await self.list(ctx)
            return

        async with ctx.typing():
            foundServer = await self.get_server_from_arg(ctx, server)
            if foundServer is None:
                return

            servername, server = foundServer

            async with aiohttp.ClientSession() as session:
                try:
                    status, response = await doaction(session, server, "update")
                    if status != 200:
                        await ctx.send(f"Не удалось запросить обновление сервера. Неверный код состояния: {status}")
                        log.debug(f"Failed to update {servername}. Wrong status code: {status} Response: {response}")
                        return

                except asyncio.TimeoutError:
                    await ctx.send("Время ожидания сервера истекло.")
                    return

                except Exception:
                    await ctx.send(
                        f"При попытке запросить обновление этого сервера произошла неизвестная ошибка, информация регистрируется в консоли...")
                    log.exception(
                        f"An error occurred while trying update server {servername}.")
                    return

            await ctx.send("Серверу было успешно отправлено указание обновиться.")

    @checks.admin()
    @commands.hybrid_command()
    async def stopserver(self, ctx: commands.Context, server: Optional[str]) -> None:
        """
        Stops a server. The server will wait for the round to end, but will not be automatically restarted.

        `<server>`: The name of the server to stop.
        """
        if not server:
            await self.list(ctx)
            return
    
        async with ctx.typing():
            foundServer = await self.get_server_from_arg(ctx, server)
            if foundServer is None:
                return
            
            servername, server = foundServer

            async with aiohttp.ClientSession() as session:
                try:
                    status, response = await doaction(session, server, "stop")
                    if status != 200:
                        await ctx.send(f"Не удалось остановить сервер. Неверный код состояния: {status}")
                        log.debug(f"Failed to stop {servername}. Wrong status code: {status} Response: {response}")
                        return

                except asyncio.TimeoutError:
                    await ctx.send("Время ожидания сервера истекло.")
                    return

                except Exception:
                    await ctx.send(
                        f"При попытке остановить этот сервер произошла неизвестная ошибка, информация регистрируется в консоли...")
                    log.exception(
                        f"An error occurred while trying to stop the server {servername}.")
                    return

            await ctx.send("Сервер успешно остановлен.")

    async def get_server_from_arg(self, ctx: commands.Context, server) -> Optional[Any]:
        selectedserver = await self.config.guild(ctx.guild).servers()

        if server not in selectedserver:
            await ctx.send("Этого сервера не существует.")
            return None

        return (server, selectedserver[server])

    @checks.admin()
    @commands.hybrid_command()
    async def restartnetwork(self, ctx: commands.Context) -> None:
        """
        Attemps to restarts all servers on the bot.
        """
        view = ConfirmView(ctx.author, disable_buttons=True, timeout=30)
        view.message = await ctx.send(":warning: Вы собираетесь перезапустить все серверы, настроенные на этом экземпляре бота, вы уверены, что хотите это сделать?", view=view)
        await view.wait()
        if not view.result:
            await ctx.send("Отменено. Никаких действий не предпринято.")
            return
        else:
            await ctx.send("Перезапуск всех серверов...")
            async with ctx.typing():
                network_data = await self.config.guild(ctx.guild).servers()

                embed = Embed(title="Перезапуск сети", description="Результаты перезапусков",
                              color=await ctx.embed_colour())

                async with aiohttp.ClientSession() as session:
                    for server_name, server_details in network_data.items():
                        try:
                            status, response = await doaction(session, server_details, "restart")
                            if status != 200:
                                embed.add_field(name=server_name, value=f":x: Неверный код состояния: {status}",
                                                inline=False)
                                log.debug(f"(Network restart) Failed to restart {server_details[0]}. "
                                          f"Wrong status code: {status} Response: {response}")
                            else:
                                embed.add_field(name=server_name, value=":white_check_mark:  Успешно", inline=False)

                        except asyncio.TimeoutError:
                            embed.add_field(name=server_name, value=":x: Время ожидания истекло", inline=False)

                        except Exception:
                            embed.add_field(name=server_name, value=":x: Неизвестная ошибка, информация регистрируется в консоли",
                                            inline=False)
                            log.exception(
                                f"(Network restart) An error occurred while trying restart server {server_name}.")

                await ctx.send("Готово", embed=embed)
