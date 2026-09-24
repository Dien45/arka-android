#!/data/data/com.termux/files/usr/bin/bash
# Jalankan di Termux setelah folder arka ada di ~/arka
set -e
pkg update -y
pkg install -y python git
pip install fastapi uvicorn httpx python-multipart
mkdir -p "$HOME/proyek"
echo
echo "Selesai. Jalankan Arka:"
echo "  cd ~/arka"
echo "  python -m uvicorn app.main:app --host 127.0.0.1 --port 8765"
echo "Lalu di Chrome HP: http://127.0.0.1:8765"
echo "Folder proyek di Pengaturan: $HOME/proyek"
