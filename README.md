# shadism (شادیسم) ⚡

[![PyPI Version](https://img.shields.io/badge/pypi-v1.0.0-blue.svg)](https://pypi.org/project/shadism/)
[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-00d2ff.svg)](https://pypi.org/project/shadism/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-shadism.spark--js.it-00f0ff.svg)](https://shadism.spark-js.it)
[![Bale Channel](https://img.shields.io/badge/Bale%20Channel-shadism__news-00a4d6.svg)](https://ble.ir/shadism_news)

**shadism** is an ultra-high-performance, modern, and fully asynchronous Python SDK and UserBot framework for the **Shad** messenger protocol.

Designed from the ground up with native `asyncio`, client-side end-to-end encryption (AES-256-CBC + RSA), rich object-oriented models, and declarative message filtering.

---

## 👨‍💻 Developer & Community

- **Author**: AmirAli khosravinejad
- **Email**: [khosravinejad.amirali@gmail.com](mailto:khosravinejad.amirali@gmail.com)
- **Official Documentation**: [https://shadism.spark-js.it](https://shadism.spark-js.it)
- **GitHub Repository**: [https://github.com/AmirAli-khosravinejad/shadism](https://github.com/AmirAli-khosravinejad/shadism)
- **Official Announcement Channel (Bale)**: [https://ble.ir/shadism_news](https://ble.ir/shadism_news)

---

## 🚀 Key Features

- **⚡ 100% Asynchronous**: Built entirely on `asyncio` and `httpx` with HTTP/2 support.
- **🔐 End-to-End Encryption**: Automated key generation, passphrases, AES-CBC payload encryption, and RSA authentication.
- **🎯 Declarative Filters**: Chainable event filters (`filters.command`, `filters.regex`, `filters.group`, `filters.private`, `&`, `|`, `~`).
- **📜 Smart Chat History**: `get_chat_history()` with automatic batch pagination, deduplication, and update fallback.
- **📁 Automated Media Streaming**: Chunked file upload (`UploadFile.ashx`) and automatic photo thumbnail generation.
- **🎙️ Voice Chat (WebRTC)**: Group and channel voice chat creation, joining, speaking activity control, and participants management.
- **🧩 Rich OOP Models**: Direct interaction with `Message`, `Chat`, and `User` objects (`await msg.reply()`, `await msg.delete()`, `await chat.send_message()`).

---

## 📦 Installation

All dependencies (`httpx`, `h2`, `cryptography`, `Pillow`) are automatically installed:

```bash
pip install shadism
```

---

## ⚡ Quickstart

### 1. Simple Echo / Ping Bot

```python
import asyncio
from shadism import Client, filters

bot = Client("0937xxxxxxx")

@bot.on_message(filters.command("ping"))
async def ping_handler(message):
    await message.reply("🏓 pong!")

@bot.on_message(filters.command("start"))
async def start_handler(message):
    await message.reply("سلام! ربات شادیسم با موفقیت راه‌اندازی شد. 🚀")

async def main():
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 2. Fetching Chat History (`get_chat_history`)

```python
from shadism import Client

bot = Client("0937xxxxxxx")

@bot.on_message
async def history_example(message):
    if message.text == "تاریخچه":
        history = await bot.get_chat_history(message.chat_guid, limit=20)
        for msg in history:
            print(f"[{msg.id}] {msg.author_guid}: {msg.text}")
```

---

### 3. Sending Photos and Files

```python
# Send photo with automatic thumbnail generation
await bot.send_photo(
    object_guid="u0DoNvd...",
    photo="banner.jpg",
    caption="تصویر جدید 🖼️"
)

# Send documents and files
await bot.send_file(
    object_guid="u0DoNvd...",
    file="document.pdf",
    caption="فایل ضمیمه 📄"
)
```

---

### 4. Interactive Two-Way Message Cleaner

```python
@bot.on_message
async def clean_messages(message):
    if message.text == "پاکسازی":
        messages = await bot.get_chat_history(message.chat_guid, limit=50)
        to_delete = [
            m.id for m in messages 
            if m.author_guid == bot.session.user_guid
        ]
        if to_delete:
            await bot.delete_messages(message.chat_guid, to_delete, delete_type="Global")
            await bot.send_message(message.chat_guid, f"عملیات انجام شد: {len(to_delete)} پیام پاک شد.")
```

---

## 📖 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
Developed by **AmirAli khosravinejad**.
