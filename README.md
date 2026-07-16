# Trade Analysis Dashboard — Setup Guide

Three files, all in this folder:
- `engine.py` — your analysis logic (same math as combined_report_v2.py)
- `app.py` — the web interface
- `requirements.txt` — the packages it needs

## Option A: Run it on your own computer first (recommended to test)

1. Install Python if you don't have it (python.org — get 3.10 or newer).
2. Open a terminal/command prompt in this folder and run:
   ```
   pip install -r requirements.txt
   streamlit run app.py
   ```
3. It opens in your browser at `http://localhost:8501`. Upload your base
   workbook in the sidebar, enter CL values, click **Run Analysis + Trade
   Score**. This only runs on YOUR machine — nothing is public yet.

## Option B: Put it online so you can reach it from any device

Streamlit Community Cloud is free and the simplest way to do this.

1. **Create a GitHub account** if you don't have one (github.com — free).
2. **Create a new repository** (e.g. `trade-dashboard`), and upload these
   three files into it (`engine.py`, `app.py`, `requirements.txt`) —
   GitHub's website lets you drag-and-drop files in, no command line needed.
   - **Important:** make the repository **Private**, not Public, since this
     contains your trading strategy logic. GitHub Free supports private repos.
3. Go to **share.streamlit.io**, sign in with your GitHub account, click
   **New app**, pick your repository, and set the main file to `app.py`.
4. **Set a password** so random people with the link can't open it:
   - In the Streamlit Cloud dashboard for your app, go to **Settings → Secrets**.
   - Add:
     ```
     APP_PASSWORD = "choose-something-only-you-know"
     ```
   - Save. The app will now show a password box before letting anyone in.
5. Click **Deploy**. After a minute or two you'll get a URL like
   `https://your-app-name.streamlit.app` — open that on your phone, laptop,
   tablet, anywhere, log in with your password, and you're in.

## Using it day to day

- Every time you open the app, upload your latest base workbook (the file
  doesn't need to live anywhere online — you upload it fresh each session,
  so your actual trading data never sits on Streamlit's servers between uses).
- Enter your 7 CL values, click Run, review the Top Pick card and the tabs
  below (same sheets as your Excel report), and download the full `.xlsx`
  if you want a saved copy.

## If you want it to auto-load your base file instead of uploading each time

That's possible (e.g. syncing from Google Drive), but it means your trading
data would need to sit in a cloud service the app can reach automatically,
which is a meaningfully bigger privacy/security surface than "upload it
yourself each time." Worth doing once you're comfortable with the app —
happy to build that next if you want it.
