from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def get_configuration() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    panel_url = os.getenv("PAINEL_URL", "").strip()
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("PAINEL_URL", panel_url))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Configuração ausente: {', '.join(missing)}")
    return token, panel_url


def build_welcome_message(first_name: str | None) -> str:
    name = (first_name or "").strip().split()[0] if (first_name or "").strip() else ""
    greeting = f"Olá, {name}!" if name else "Olá!"
    return (
        f"{greeting}\n\n"
        "Seja bem-vindo(a)!\n\n"
        "Para acessar sua conta e começar a usar o sistema, entre no seu painel pelo botão abaixo.\n\n"
        "Seu acesso é pessoal."
    )


def build_panel_keyboard(panel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("ACESSAR PAINEL", url=panel_url)]]
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    first_name = update.effective_user.first_name if update.effective_user else None
    _, panel_url = get_configuration()
    await message.reply_text(
        build_welcome_message(first_name),
        reply_markup=build_panel_keyboard(panel_url),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error_name = type(context.error).__name__ if context.error else "UnknownError"
    logger.error("Erro ao processar atualização do Telegram: %s", error_name)


async def post_init(application: Application) -> None:
    logger.info("Bot Telegram autenticado e iniciado corretamente.")


def build_application(token: str | None = None) -> Application:
    configured_token, _ = get_configuration()
    application = Application.builder().token(token or configured_token).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    logger.info("Bot Telegram iniciando polling.")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,
    )


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("telegram").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    main()
