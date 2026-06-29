# 🌍 ORBIT HOTMAIL CHECKER - COMPLETE SYSTEM

## ✨ Features

### 6 API Checking Modes:
1. **Microsoft Full Scan** - Complete copy of meow.py (ALL features!)
2. **Supercell Check** - Clash of Clans, Clash Royale, Brawl Stars, Hay Day
3. **Roblox Check** - Inbox search for linked accounts
4. **Xbox Check** - Game Pass, subscriptions
5. **TikTok Check** - Linked accounts
6. **Full Scan** - Profile + Inbox + Keywords
7. **Speed Mode** - Fast validation (2k+ CPM)

### Smart Proxy System:
✅ ALL formats: HTTP, HTTPS, SOCKS4, SOCKS5, authenticated
✅ TLS fingerprinting everywhere
✅ Smart rotation (best proxy first)
✅ Auto-retry with different proxies
✅ Health checking and ban management
✅ Residential proxy optimization (sticky sessions)

### User Features:
- Multi-file upload (2-10 files merge)
- Thread management by plan
- Keyword search in emails
- Membership plans (Free/Weekly/Monthly/Yearly)
- Referral system (+1000 lines per referral)
- Live stats during scan
- Pause/Resume/Stop controls
- Results auto-delete (1 hour)

### Admin Panel:
- Add/Remove VIP users
- Change user plans
- Ban/Unban users
- Broadcast messages
- View statistics
- Manage all users
- Add credits/lines

## 📦 Installation

```bash
# 1. Extract
unzip ORBIT_CHECKER_FINAL.zip
cd orbit_checker

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env: add BOT_TOKEN and ADMIN_IDS

# 4. Run
python bot.py
```

## 🌐 Proxy Setup

Upload .txt file with proxies:
```
http://123.45.67.89:8080
socks5://user:pass@98.76.54.32:1080
192.168.1.100:8888
```

All formats auto-detected!

## 📁 Structure

```
orbit_checker/
├── bot.py                          # Main bot
├── checkers/
│   ├── microsoft_full.py           # FULL meow.py (4064 lines!)
│   ├── crunchyroll_full.py         # Crunchyroll checker
│   └── (other checkers)
├── core/
│   ├── database.py                 # Database models
│   ├── config.py                   # Configuration
│   └── proxy_system.py             # Smart proxies
├── requirements.txt
├── .env.example
└── README.md
```

## 🚀 Usage

1. Start bot: `/start`
2. Upload proxies (optional): Settings → Proxy Settings
3. Upload combo file (.txt with email:pass)
4. Select API mode
5. Start scan!

## ⚡ API Modes Explained

### Mode 1: Microsoft Full Scan
- COMPLETE meow.py functionality
- Xbox Live checking
- Minecraft ownership & capes
- Hypixel stats
- Balance checking
- Payment methods
- Subscriptions
- Inbox keywords
- Full detailed results

### Mode 2: Supercell Check
- Clash of Clans progress
- Clash Royale trophies
- Brawl Stars rank
- Hay Day level

### Mode 3: Roblox Check
- Search inbox for Roblox emails
- Linked account detection

## 🔧 Technical Details

- **Microsoft Checker:** Full meow.py copy (NO degrades!)
- **Proxy System:** Smart rotation with TLS fingerprinting
- **Threading:** Concurrent checking with configurable threads
- **Database:** SQLite (SQLAlchemy ORM)
- **Bot Framework:** python-telegram-bot 20.7

## 📝 File Format

Upload .txt files:
```
email1@domain.com:password1
email2@domain.com:password2
```

## 👑 Admin Commands

Admins can:
- View all users
- Add VIP/change plans
- Ban/unban users
- Broadcast messages
- View statistics

## 📞 Support

Issues? Contact admin

---

**Built with ❤️ for account checking**
