# Zenvest

Zenvest is a secure, advanced financial intelligence web application designed to provide users with comprehensive market analysis, portfolio optimization, and AI-driven insights.

## Features

- **AI Portfolio Analysis:** Get context-aware, holding-specific investment insights using Gemini AI.
- **Advanced Technical Analysis:** Live technical indicators (RSI, MACD, Bollinger Bands) using `pandas-ta`.
- **Options Chain Viewer:** Interactive options chain for NSE derivatives.
- **Sector Heatmap:** Visual monitor for market performance across various sectors.
- **Secure Authentication:** Robust JWT-based authentication with bcrypt password hashing and secure cookie policies.
- **Data Security:** Row Level Security (RLS) via Supabase, with protections against XSS and SQL injection.

## Project Structure

- `templates/`: HTML templates for the frontend views.
- `static/`: Static assets including CSS, JS, and images.
- `.env`: Environment variables (API keys, database credentials - *not included in version control*).

## Getting Started

1. **Clone the repository**
2. **Set up a virtual environment** (recommended)
   ```bash
   python -m venv venv
   source venv/bin/activate # On Windows use `venv\Scripts\activate`
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Environment Variables**
   Create a `.env` file in the root directory and add your required API keys and Supabase credentials.
5. **Run the Application**
   ```bash
   python app.py
   ```

## Technologies Used

- Python (Backend)
- HTML / Vanilla CSS / JavaScript (Frontend)
- Supabase (Database & Authentication)
- Gemini AI (Analysis)
- `pandas-ta` (Technical Indicators)
