#!/usr/bin/env bash
set -o errexit

# cryptography/cffi (see requirements.txt's comment on the pdfplumber ->
# pdfminer.six -> cryptography chain): this container ships a
# Debian/apt-installed 'cryptography' package that pip has no RECORD file
# for and refuses to uninstall -- a plain `pip install -r requirements.txt`
# fails outright on this one dependency with "Cannot uninstall
# cryptography ...: installed by debian", which (with `set -o errexit`
# above) aborts the ENTIRE build and silently leaves the previous,
# still-broken deploy running instead of failing loudly. Force a clean,
# pip-managed install of just these two packages first so the real
# requirements.txt install below never hits that conflict.
pip install --ignore-installed cryptography==42.0.8 cffi==2.1.1
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py create_admin_baseera
