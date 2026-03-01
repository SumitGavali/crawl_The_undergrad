# KnowledgeOS — Enterprise File Search Prototype

> **One button. Any folder. Ask anything. Get answers from your files.**

Local semantic search engine for company documents — PDFs, Word docs, Excel sheets.  
Built with Python + HuggingFace (free, offline, no OpenAI key needed).

---

## What This Does

```
You type:   "what is the oil change interval for GA 37 compressor"
It finds:   Engineering/GA37_Maintenance_Manual.pdf — Page 47 — 94% match
            "Oil change interval is every 4000 operating hours or 12 months..."
```

Instead of ctrl+F across 300 PDFs — one search box finds answers by **meaning**, not just keywords.


### Indexing your folder (Live Crawl tab)

1. Click **Live Crawl** tab in the top nav
2. Enter the full path to your folder, for example:
   - Windows: `C:\Users\Sumit\Documents\CompanyFiles`
   - Mac/Linux: `/Users/sumit/Documents/CompanyFiles`
   - Or use the demo folder: `./demo_docs`
3. Click **Start Indexing**
4. Watch the terminal — it shows every file being read, extracted, and embedded in real time
5. When you see **"Index complete — Ready to search"**, switch to the Search tab

> The indexing creates `backend/data/vectors.pkl` and `backend/data/metadata.json`.  
> These persist on disk — you only need to re-index when files change.

### Searching (Search tab)

1. Type any question in plain English
2. Hit Enter or click Search
3. Results show:
   - Which file the answer came from (full path)
   - The exact excerpt
   - Relevance % score
4. Click **"View source proof"** under any result to see:
   - Full file path
   - Page number
   - **"Open Original File"** button — opens the file in your OS

---

## Supported File Types

| Type | Extension | Notes |
|------|-----------|-------|
| PDF | `.pdf` | Text-based PDFs (not scanned images) |
| Word | `.docx`, `.doc` | All Word documents |
| Excel | `.xlsx`, `.xls` | All sheets extracted |
| Text | `.txt`, `.csv` | Plain text and CSV files |



## How It Works (Technical)

```
INDEXING (one time):
Folder → walk all files
       → extract text (pdfplumber / python-docx / openpyxl)
       → split into 400-word overlapping chunks
       → encode each chunk → vector (HuggingFace all-MiniLM-L6-v2)
       → save vectors.pkl + metadata.json to disk

SEARCHING (every query):
Question → encode → question vector
         → cosine similarity vs all chunk vectors
         → return top 5 closest matches
         → show file, excerpt, page, score
```

The key: same model encodes both documents and questions, so meaning is compared — not just keywords.

---

## Re-indexing

Run a new crawl any time your files change:
- Go to Live Crawl tab
- Enter the folder path again
- Click Start Indexing
- Old index is automatically replaced


## Roadmap (Next Steps as a Product)

| Feature | What it adds |
|---------|-------------|
| LLM answer synthesis | Generates a paragraph answer from top chunks, not just excerpts |
| SharePoint connector | Crawls SharePoint via Microsoft Graph API |
| Network drive support | Crawls `\\SERVER\Share` paths on Windows |
| Multi-folder index | Index multiple folders into one searchable index |
| Scheduled re-indexing | Auto re-index nightly via cron/Task Scheduler |
| Azure OpenAI upgrade | Replace local model with Azure for enterprise compliance |
| Microsoft Copilot plugin | Register as a Copilot connector for Teams integration |
| User authentication | Azure AD login for enterprise deployment |

---

