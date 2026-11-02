"""Run each scheduled job once per period, even with several worker processes (each has its own scheduler)."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import JobRun


def claim_run(db: Session, job_name: str, run_key: str) -> bool:
    """True for the first process to claim (job_name, run_key); every other process gets False and skips the run."""
    db.add(JobRun(job_name=job_name, run_key=run_key))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
