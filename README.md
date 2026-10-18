# Gemma 4 Chat

```bash
git clone https://github.com/maamspy/hacktoberfest-hddhaka-2026.git
cd hacktoberfest-hddhaka-2026
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Get a free key at https://aistudio.google.com/apikey and put it in `.env`

```
GOOGLE_AI_STUDIO_API_KEY=your-key-here
```

```bash
streamlit run app.py
```

Open the URL Streamlit prints and start chatting. There is no key field in the app, so restart it after changing the key.
