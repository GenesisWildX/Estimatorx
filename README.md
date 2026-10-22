# Duperman Services Job Estimator

A mobile-friendly Streamlit estimator for customer quotes, job tracking, actual
costs, photos, receipts, and printable PDF documents. Estimates and attachments
are stored in the workspace under `data/`, so they remain available after the
Streamlit server restarts.

## Start the website

In the Codespace terminal, run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the forwarded Streamlit port in the Codespaces Ports tab. On an iPad, open
the forwarded address in Safari. Use **New / Edit Estimate** to create or update
an estimate, and **Jobs & Files** to update its status and actual expenses,
upload job photos or receipts, and download PDFs.

The SQLite database is `data/duperman_jobs.db`; uploaded files are in
`data/uploads/`. These runtime files are intentionally excluded from Git.
Python's built-in SQLite support is used, so no separate database service or
paid AI subscription is required.