"""Prepare one persistent QA account and small PDFs in the isolated preview."""
import json
import secrets
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = Path.home() / ".local/state/deepread/preview"
ACCOUNT = STATE / "testing/account.json"
ACCOUNT.parent.mkdir(parents=True, exist_ok=True)
if not ACCOUNT.exists():
    ACCOUNT.write_text(json.dumps({"username": "deepread.qa", "password": secrets.token_urlsafe(32)}))
    ACCOUNT.chmod(0o600)
account = json.loads(ACCOUNT.read_text())
for name in ["guide", "research", "control"]:
    shutil.copyfile(REPO / "e2e/fixtures/e2e-sample.pdf", STATE / f"data/qa-{name}.pdf")
script = '''
from app.database import SessionLocal
from app.models import User, Document
from app.services.auth_service import get_password_hash
from pathlib import Path
account = ACCOUNT_DATA
with SessionLocal() as db:
    user = db.query(User).filter(User.username == account["username"]).first()
    if not user:
        user = User(username=account["username"], password_hash=get_password_hash(account["password"]), is_admin=False)
        db.add(user)
        db.flush()
    for slug, title in [("guide", "QA Notebook Guide.pdf"), ("research", "QA Research Notes.pdf"), ("control", "QA Control.pdf")]:
        document = db.query(Document).filter(Document.user_id == user.id, Document.title == title).first()
        if not document:
            document = Document(user_id=user.id, title=title, format="pdf")
            db.add(document)
        document.file_path = f"/app/data/qa-{slug}.pdf"
        document.file_size = Path(document.file_path).stat().st_size
        document.num_pages = 2
        document.processing_status = "completed"
    db.commit()
print("Persistent QA account and three small PDF fixtures ready")
'''.replace("ACCOUNT_DATA", repr(account))
subprocess.run(
    ["docker", "compose", "--env-file", str(STATE / "preview.env"), "-f", "docker-compose.preview.yml", "exec", "-T", "api", "python", "-"],
    cwd=REPO, input=script, text=True, check=True,
)
