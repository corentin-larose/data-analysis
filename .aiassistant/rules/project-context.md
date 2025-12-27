---
apply: off
---

# Project context

## Purpose 

1 - Recursively read PST directories
2 - Store from, to, date, title and email content in a MariaDb database
3 - Create a deduplication hash from email information (same email could be found in 2 different PST directories) 
4 - Store uncompressed attachments under a directory naled after the deduplication hash