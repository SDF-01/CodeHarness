---
name: fullstack
description: Build a page and a local API together. Use when the user asks for a full stack app, an api, a backend, or a frontend with a server.
---
Write index.html and server.py in the current folder. server.py uses http.server from the Python standard library, serves this folder, and answers GET /api/health with JSON on port 8766. index.html calls /api/health. No pip install. No npm install. Do not use tkinter.
