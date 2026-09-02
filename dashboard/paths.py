"""
Canonical on-disk locations for the user-facing "workspace" and "approved
plans" folders, shared by every view/endpoint that reads or writes them.

Centralized here after dashboard/api_views.py's save_file_api was found
writing new files under MEDIA_ROOT/workspace while every other workspace
endpoint (views.py's workspace_view/download_workspace_file/
delete_workspace_file, api_views.py's workspace_files_api) reads from
BASE_DIR/sandbox/workspace -- a different physical directory. A file an
agent "saved" was silently invisible in the workspace list/download/delete
views. Import get_workspace_dir()/get_approved_plans_dir() instead of
recomputing the join inline, so the paths can't drift apart again.
"""
import os

from django.conf import settings


def get_workspace_dir():
    path = os.path.join(settings.BASE_DIR, 'sandbox', 'workspace')
    os.makedirs(path, exist_ok=True)
    return path


def get_approved_plans_dir():
    path = os.path.join(settings.BASE_DIR, 'sandbox', 'approved_plans')
    os.makedirs(path, exist_ok=True)
    return path
