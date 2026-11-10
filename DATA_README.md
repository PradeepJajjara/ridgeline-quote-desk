# Data pack: FDE Challenge (AI/ML & Data): Ridgeline Quote Desk

All data is fictional.

| File | What it is |
|---|---|
| `rfqs/` | 6 incoming RFQs: email text, PDF, Excel, a photo of a handwritten list, a forwarded email chain, and a messy one |
| `products.csv` | About 200 Ridgeline products |
| `cross_reference.csv` | Competitor part numbers mapped to Ridgeline part numbers (incomplete on purpose) |
| `customers.csv` | Ridgeline customers |
| `answer_key/` | Expected line items and matches for `rfq_01`, `rfq_02` and `rfq_03`. Only your evaluation script may read it, never your pipeline |
| `ai_logging_prompt.txt` | The first message for your AI assistant. It sets up the interaction log in `.chat-history/log.md` |
| `check_submission.py` | Checks your deliverables and packs them into the zip you submit. Copy it to your repository root. You can run it any time; run it before each push and as the last step of your recording |

There is no answer key for `rfq_04` to `rfq_06`. Run your pipeline on `rfq_05` and `rfq_06` as they are. The photo (`rfq_04`) can be a stub; the challenge description shows the stub format. Every RFQ still needs its output file.
