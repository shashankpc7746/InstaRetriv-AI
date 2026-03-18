# InstaRetriv AI

InstaRetriv AI is your personal WhatsApp-based document retrieval assistant.
Ask naturally, and get your file back instantly.

## Try These Prompts

- Send my resume
- Give CV
- I need my Aadhaar
- Send latest certificate

## How It Works

1. You send a WhatsApp message.
2. The FastAPI webhook receives your request.
3. The matcher checks tags and keywords.
4. The matched file is fetched from storage.
5. The document is sent back to WhatsApp.

## MVP Features

- Single-user personal assistant flow
- Twilio WhatsApp Sandbox integration
- Document upload with manual tags
- Keyword-based retrieval (fuzzy matching upgrade-ready)
- Request and response logging for debugging

## Planned Tech Stack

- Backend: FastAPI (Python)
- WhatsApp API: Twilio WhatsApp Sandbox
- Database: MongoDB or Firebase Firestore
- Storage: Local storage for MVP, AWS S3/Firebase Storage later
- NLP: Simple keyword matching

## API Plan

- POST /upload
	- Upload document with tags and category
- POST /webhook
	- Receive WhatsApp messages and trigger retrieval
- Internal get-document logic
	- Match request to stored document metadata

## Quick Project Status

- Roadmap is defined.
- MVP implementation begins phase-by-phase.
- Current focus: setup, upload pipeline, and webhook wiring.

## Project Vision

Reduce friction in accessing important personal documents through simple natural language chat over WhatsApp.

## Coming Soon

- Core FastAPI scaffold
- Upload endpoint (/upload)
- WhatsApp webhook endpoint (/webhook)
- Retrieval matcher with tag + synonym support
- End-to-end MVP demo on Twilio Sandbox