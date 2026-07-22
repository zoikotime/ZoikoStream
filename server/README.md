# ZoikoStream API

Production-ready live streaming backend built with FastAPI, PostgreSQL, SQLAlchemy, JWT Authentication and LiveKit.

---

# Technology Stack

Backend
- FastAPI
- SQLAlchemy 2.0
- PostgreSQL
- Pydantic v2
- JWT Authentication

Streaming
- LiveKit Cloud
- WebRTC

Infrastructure
- PostgreSQL
- Redis (next phase)
- Google Cloud Storage (recordings)
- Cloud Run (deployment)

---

# Project Structure

server/
│
├── app/
│   ├── models/
│   ├── routers/
│   ├── schemas/
│   ├── services/
│   ├── security.py
│   ├── config.py
│   ├── db.py
│   └── main.py
│
├── requirements.txt
├── .env
└── README.md

---

# Installation

```bash
cd server

python -m venv venv

# Windows
venv\Scripts\activate

pip install -r requirements.txt

uvicorn app.main:app --reload
```

Swagger

```
http://localhost:8000/docs
```

---

# Environment Variables

Create a `.env`

```env
DATABASE_URL=postgresql://username:password@host/database

SECRET_KEY=your_secret_key

SUPER_ADMIN_EMAIL=info@zoikostream.com

ACCESS_TOKEN_HOURS=24

REMEMBER_TOKEN_DAYS=30

LIVEKIT_URL=https://YOUR_PROJECT.livekit.cloud

LIVEKIT_API_KEY=xxxxxxxx

LIVEKIT_API_SECRET=xxxxxxxx

RESEND_API_KEY=re_your_resend_api_key

# Use a verified sender in Resend, not the onboarding default, for production email.
MAIL_FROM=ZoikoStream <noreply@zoikostream.com>
```

---

# Database

Current Tables

- users
- channels
- streams

Upcoming

- videos
- recordings
- chat_messages
- followers
- subscriptions
- notifications
- stream_views
- analytics

---

# Authentication

JWT Bearer Authentication

Available APIs

POST /auth/register

POST /auth/login

POST /auth/forgot-password

POST /auth/reset-password

GET /auth/me

---

# Channels

Implemented

POST /channels

GET /channels

GET /channels/{id}

PUT /channels/{id}

DELETE /channels/{id}

---

# Streams

Implemented

POST /streams

GET /streams

GET /streams/{id}

PUT /streams/{id}

DELETE /streams/{id}

POST /streams/{id}/start

POST /streams/{id}/stop

GET /streams/{id}/token

---

# Live Streaming Flow

1. User registers.
2. User creates a channel.
3. User creates a stream.
4. User starts the stream.
5. Backend creates a LiveKit room.
6. Host receives a LiveKit access token.
7. Viewers request a viewer token.
8. Both connect to the same LiveKit room.

---

# Current Features

✅ JWT Authentication

✅ User Management

✅ Channel Management

✅ Stream CRUD

✅ Stream Keys

✅ Stream Start

✅ Stream Stop

✅ LiveKit Integration

✅ Host Token Generation

✅ Viewer Token Generation

---

# Development Status

Completed

- Authentication
- Authorization
- Channels
- Streams
- LiveKit Integration

In Progress

- React Frontend
- Live Video Publishing
- Viewer Playback

Upcoming

- Live Chat
- Recording
- Google Cloud Storage
- Notifications
- Search
- Analytics
- Moderation
- Payments
- Creator Dashboard

---

# API Documentation

Swagger

http://localhost:8000/docs

ReDoc

http://localhost:8000/redoc

---

# Production Architecture

Client (React)

↓

FastAPI

↓

PostgreSQL

↓

LiveKit Cloud

↓

Google Cloud Storage

↓

Redis

↓

Cloud Run

---

# License

Copyright © ZoikoStream.

All rights reserved.