                ┌──────────────────────────┐
                │   Apple Health Export    │
                │   Whoop CSV Exports      │
                └────────────┬─────────────┘
                             │ (local files)
                             ▼
                    ┌───────────────────┐
                    │   Leo Parsers     │
                    │ (no network I/O)  │
                    └────────┬──────────┘
                             │
                             ▼
                    ┌───────────────────┐
                    │ SQLite Database   │
                    │ ~/.leo-health     │
                    └────────┬──────────┘
                             │
                             ▼
                    ┌───────────────────┐
                    │ Local Dashboard   │
                    │ 127.0.0.1 only    │
                    └────────┬──────────┘
                             │
                             ▼
                        ┌────────┐
                        │ Browser │
                        │ (local) │
                        └────────┘
