"""Run: python -m streamlit run custom_streamlit_app.py"""
import hashlib
import hmac
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import (
    OpenAI, AuthenticationError, PermissionDeniedError, NotFoundError,
    RateLimitError, APIConnectionError, APITimeoutError, APIError,
)

load_dotenv(Path(__file__).resolve().parent / '.env', override=True)

DB_PATH = Path(__file__).resolve().parent / 'nova_chats.sqlite3'


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('CREATE TABLE IF NOT EXISTS conversations '
                   '(id TEXT PRIMARY KEY, title TEXT NOT NULL, updated TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS messages '
                   '(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, '
                   "role TEXT NOT NULL CHECK(role IN ('user', 'assistant')), content TEXT NOT NULL, "
                   'FOREIGN KEY(conversation_id) REFERENCES conversations(id))')
        with db:
            yield db
    finally:
        db.close()


def list_chats():
    with connect() as db:
        return db.execute('SELECT id, title FROM conversations ORDER BY updated DESC').fetchall()


def load_chat(chat_id):
    with connect() as db:
        return [{'role': role, 'content': content} for role, content in db.execute(
            'SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id', (chat_id,))]


def save_chat(chat_id, messages):
    if not messages:
        return chat_id
    chat_id = chat_id or uuid.uuid4().hex
    title = ' '.join(messages[0]['content'].split())[:55] or 'New conversation'
    with connect() as db:
        db.execute('INSERT INTO conversations VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE '
                   'SET title=excluded.title, updated=excluded.updated',
                   (chat_id, title, datetime.now(timezone.utc).isoformat()))
        db.execute('DELETE FROM messages WHERE conversation_id=?', (chat_id,))
        db.executemany('INSERT INTO messages(conversation_id, role, content) VALUES (?, ?, ?)',
                       [(chat_id, m['role'], m['content']) for m in messages])
    return chat_id

st.set_page_config(page_title='Nova | Your AI workspace', layout='wide')

st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stApp {font-family: Inter, sans-serif;}
.stApp {background: radial-gradient(ellipse at 70% 0%, #182445 0%, #0b1020 55%); color:#e8edf8;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stSidebar"] {background:#10182b; border-right:1px solid #26314b;}
[data-testid="stSidebar"] * {color:#e8edf8;}
.block-container {max-width:1080px; padding-top:3rem; padding-bottom:3rem;}
.brand {font-size:1.5rem; font-weight:800; letter-spacing:.12em; color:#a5b4fc;}
.eyebrow {color:#a5b4fc; font-size:.75rem; letter-spacing:.18em; font-weight:700; margin-top:1rem;}
.hero {font-size:clamp(2rem,5vw,3.5rem); line-height:1.15; font-weight:800; letter-spacing:-.045em; margin:1rem 0; color:#f2f5ff;}
.subtitle {color:#a5b3cf; font-size:1.05rem; line-height:1.7; max-width:620px; margin-bottom:2rem;}
.pill {display:inline-block; padding:7px 12px; border:1px solid #354566; border-radius:30px; color:#b5c8ed; font-size:.8rem;}
[data-testid="stChatMessage"] {background:#141e33; border:1px solid #283550; border-radius:18px; margin:1rem 0; padding:1.1rem;}
[data-testid="stBottom"] > div {background:#0b1020;}
[data-testid="stChatInput"] {border:1px solid #52638d; border-radius:16px;}
.stButton > button, .stDownloadButton > button {border-radius:12px; border:1px solid #354566; background:#1a2843; color:#e8edf8;}
.stButton > button:hover {border-color:#a5b4fc; color:#c7d2fe;}
textarea, input {color:#e8edf8 !important; background:#17233a !important;}
</style>''', unsafe_allow_html=True)

MODEL = 'gpt-5.2'
DEFAULT_SYSTEM = ('You are Nova, a thoughtful and practical assistant. Give accurate, clear '
                  'answers. Match the user\'s language, including Roman Urdu. Ask for clarification '
                  'when needed and acknowledge uncertainty. Use Markdown when helpful.')



def api_error(exc):
    if isinstance(exc, AuthenticationError):
        return 'API key rejected. Enter a valid OpenAI API key in Workspace settings.'
    if isinstance(exc, (PermissionDeniedError, NotFoundError)):
        return 'Is API key ko GPT-5.2 ya required endpoint ka access nahi mila. Project permissions check karein.'
    if isinstance(exc, RateLimitError):
        return 'API limit ya quota khatam hai. Billing / usage check karein, ya thori dair baad try karein.'
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return 'OpenAI se connection nahi hua. Internet check karein aur dobara try karein.'
    return 'AI service abhi response nahi de saki. Dobara try karein.'


# Optional separate owner password; never use your API key as a login password.
password = os.getenv('APP_PASSWORD', '').strip()
password_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
if password and st.session_state.get('login_hash') != password_hash:
    st.markdown('<div class="brand"> NOVA</div>', unsafe_allow_html=True)
    st.title('Your private AI workspace')
    st.caption('Enter your workspace password to continue.')
    with st.form('login'):
        entered = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Unlock workspace', use_container_width=True)
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state.login_hash = password_hash
            st.rerun()
        else:
            time.sleep(1)
            st.error('Password sahi nahi hai.')
    st.stop()

# The workspace always opens. API credentials are needed only for AI replies.
key = os.getenv('OPENAI_API_KEY', '').strip()

st.session_state.setdefault('messages', [])
st.session_state.setdefault('pending_prompt', None)
st.session_state.setdefault('chat_id', None)


def persist():
    st.session_state.chat_id = save_chat(st.session_state.chat_id, st.session_state.messages)


with st.sidebar:
    st.markdown('<div class="brand"> NOVA</div>', unsafe_allow_html=True)
    st.caption('A little clarity. A lot of possibility.')
    st.divider()
    if st.button('＋ New conversation', use_container_width=True):
        st.session_state.messages = []
        st.session_state.chat_id = None
        st.session_state.pending_prompt = None
        st.rerun()
    st.markdown('**Saved conversations**')
    for chat_id, title in list_chats():
        if st.button(title, key='chat_' + chat_id, use_container_width=True):
            st.session_state.chat_id = chat_id
            st.session_state.messages = load_chat(chat_id)
            st.session_state.pending_prompt = None
            st.session_state.pop('retry_requested', None)
            st.rerun()
    st.divider()
    st.markdown('**Workspace settings**')
    entered_key = st.text_input('OpenAI API key', type='password', key='api_key_input',
                               placeholder='sk-...',
                               help='Used only for this browser session. Leave blank to use the .env key.')
    key = entered_key.strip() or key
    st.caption('Using your session key.' if entered_key.strip() else
               ('Using the configured .env key.' if key else 'Add a key to enable AI replies.'))
    if st.button('Test API key', disabled=not key, use_container_width=True):
        try:
            with st.spinner('Checking API key...'):
                with OpenAI(api_key=key, timeout=20.0, max_retries=0) as test_client:
                    test_client.models.retrieve(MODEL)
            st.success('Key accepted and model accessible. Billing is checked when sending a message.')
        except APIError as exc:
            st.error(api_error(exc))
    st.caption('Model · GPT-5.2')
    st.caption('API key configured · checked when sending' if key else 'Preview mode · add an API key for AI replies')
    role = st.text_area('Assistant instructions', DEFAULT_SYSTEM, height=180)
    tone = st.selectbox('Response style', ['Balanced', 'Concise', 'Detailed'])
    st.caption('Instructions apply to the next reply.')
    st.divider()
    transcript = '\n\n'.join(f"{m['role'].upper()}\n{m['content']}" for m in st.session_state.messages)
    st.download_button('↓ Download conversation', transcript, 'nova-chat.txt',
                       mime='text/plain', disabled=not transcript, use_container_width=True)
    if password and st.button('Lock workspace', use_container_width=True):
        st.session_state.clear()
        st.rerun()
    st.caption('Chats are saved on this computer. Everyone with workspace access shares this history. Messages are sent to OpenAI for replies.')

st.markdown('<span class="pill">NOVA WORKSPACE · GPT-5.2</span>', unsafe_allow_html=True)
if not st.session_state.messages:
    st.markdown('<div class="eyebrow">YOUR IDEAS, WITH MOMENTUM</div>'
                '<div class="hero">Make room for<br>your next great idea.</div>'
                '<div class="subtitle">Write something better, untangle a problem, or learn something new. '
                'Start with a thought. We’ll take it from there.</div>', unsafe_allow_html=True)
    examples = [('✎ Write & polish', 'Help me write a professional email. Ask me who it is for and what I want to say.'),
                ('⌘ Code & solve', 'Help me debug my code. Ask me to share the code and error.'),
                ('◇ Learn & explore', 'Help me learn a new topic step by step. Ask which topic I want to learn.')]
    for col, (label, prompt) in zip(st.columns(3), examples):
        with col:
            if st.button(label, use_container_width=True):
                st.session_state.pending_prompt = prompt
else:
    st.markdown('## Your conversation')

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

typed = st.chat_input('Ask Nova anything…', max_chars=12000)
prompt = typed or st.session_state.pending_prompt
st.session_state.pending_prompt = None
retry = st.session_state.pop('retry_requested', False)
if st.session_state.messages and st.session_state.messages[-1]['role'] == 'user' and not prompt:
    retry = st.button('↻ Retry last message') or retry

if (prompt or retry) and not key:
    st.info('Add your OpenAI API key in Workspace settings, then send your message again.')

if (prompt or retry) and key:
    if prompt:
        # Replace an unanswered prompt, so failed requests do not accumulate.
        if st.session_state.messages and st.session_state.messages[-1]['role'] == 'user':
            st.session_state.messages.pop()
        st.session_state.messages.append({'role': 'user', 'content': prompt})
        persist()
        with st.chat_message('user'):
            st.markdown(prompt)
    style = {'Balanced': 'Use moderate detail.', 'Concise': 'Be brief and direct.',
             'Detailed': 'Explain thoroughly with useful examples.'}[tone]
    # Last 12 exchanges limit growth; full history remains visible/downloadable.
    history = st.session_state.messages[-24:]
    if history and history[0]['role'] == 'assistant':
        history = history[1:]
    with st.chat_message('assistant'):
        slot = st.empty()
        try:
            client = OpenAI(api_key=key, timeout=60.0, max_retries=1)
            def tokens():
                with client.chat.completions.create(
                    model=MODEL,
                    messages=[{'role': 'system', 'content': (role.strip() or DEFAULT_SYSTEM) + '\n' + style}] + history,
                    reasoning_effort='none', max_completion_tokens=3000,
                    stream=True, store=False,
                ) as stream:
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
            with slot.container():
                response = st.write_stream(tokens())
            if not isinstance(response, str) or not response.strip():
                slot.warning('Khaali response mila. Retry last message dabayein.')
            else:
                st.session_state.messages.append({'role': 'assistant', 'content': response})
                persist()
                st.rerun()
        except APIError as exc:
            slot.error(api_error(exc))
            def request_retry():
                st.session_state.retry_requested = True
            st.button('↻ Retry last message', key='retry_after_error', on_click=request_retry)

st.caption('Nova can make mistakes. Review important information.')
