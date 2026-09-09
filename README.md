# CodeIntelligence Phase 1

## Create PostgreSQL database

Create a database named:

    codeintelligence

## Create .env

Copy `.env.example` to `.env` and update the password and Java project path.

Example:

    APP_NAME=CodeIntelligence
    APP_VERSION=1.0.0

    DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/codeintelligence

    JAVA_PROJECT_PATH=D:\AIlearning\employee-management-service

## Install

    py -m venv .venv
    .venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt

## Run

    uvicorn main:app --reload

## Test

    GET /health
    GET /api/scenarios
    POST /api/code/scan

`POST /api/code/scan` requires no request body.
The Java project path comes from `.env`.
