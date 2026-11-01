# Guitar 0 Telegram Bot

[![CI](https://github.com/guitar0-net/tgbot/actions/workflows/test.yml/badge.svg)](https://github.com/guitar0-net/tgbot/actions/workflows/test.yml)
[![Bandit](https://github.com/guitar0-net/tgbot/actions/workflows/bandit.yml/badge.svg)](https://github.com/guitar0-net/tgbot/actions/workflows/bandit.yml)
[![codecov](https://codecov.io/gh/guitar0-net/tgbot/branch/main/graph/badge.svg)](https://codecov.io/gh/guitar0-net/tgbot)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3](https://img.shields.io/badge/aiogram-3.x-2CA5E0.svg)](https://docs.aiogram.dev/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

## ✨ Features

- 📚 **Lessons** — browse and read guitar lessons pulled from the Guitar 0 API
- 🎸 **Chords** — look up chord fingerings/diagrams on demand
- ✍️ **Feedback** — send feedback straight from the chat with

## 🧰 Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3.13+ |
| Bot framework | [aiogram](https://docs.aiogram.dev/) 3 |
| Database | SQLite |
| Config | `python-dotenv` |
| Data source | [Guitar 0 API](https://api.guitar0.net/api/v1/docs/redoc/) |
| Linting | [ruff](https://docs.astral.sh/ruff/) |
| Testing | [pytest](https://docs.pytest.org/) + [pytest-cov](https://pytest-cov.readthedocs.io/) |

## 🚀 Getting Started

### Installation

```sh
git clone https://github.com/zhenya194/guitar0-tgbot.git
cd guitar0-tgbot
make install
```

### Configuration

Copy the example environment file and fill in your bot token:

```sh
cp .env.example .env
```

```dotenv
BOT_TOKEN=your-telegram-bot-token
ADMIN_ID=123456789,987654321
```

if you use Docker and do not have `bot_database.db` :

#### Linux/MacOS

```sh
touch bot_database.db
```

#### Windows

```sh
New-Item -ItemType File bot_database.db
```

### Run

```sh
make run
```
or
```sh
python main.py
```

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## 📄 License

Licensed under the [GNU GPLv3](LICENSE).
