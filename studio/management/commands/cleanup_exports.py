"""Remove expired export outputs and orphaned temporary files.

Usage::

    python manage.py cleanup_exports --days 7 --dry-run

Completed export files older than ``--days`` are deleted (the job rows stay
for history, marked without an output file). Failed jobs older than the same
window are hard-deleted. Temporary render/upload directories are removed.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from studio.models import ExportJob


class Command(BaseCommand):
    help = "Delete expired export files and old failed export jobs."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--days", type=int, default=7, help="Age threshold in days.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be deleted."
        )

    def handle(self, *args, **options) -> None:
        days = max(0, options["days"])
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        expired_outputs = ExportJob.objects.filter(
            status=ExportJob.Status.COMPLETED,
            completed_at__lt=cutoff,
            output_file__gt="",
        )
        count = 0
        for job in expired_outputs.iterator():
            count += 1
            if not dry_run:
                job.output_file.delete(save=False)
                job.save(update_fields=["output_file"])
        self.stdout.write(f"{'Would delete' if dry_run else 'Deleted'} {count} export file(s).")

        old_failed = ExportJob.objects.filter(
            status=ExportJob.Status.FAILED, completed_at__lt=cutoff
        )
        failed_count = old_failed.count()
        if not dry_run:
            for job in old_failed.iterator():
                job.output_file.delete(save=False)
            old_failed.delete()
        self.stdout.write(
            f"{'Would delete' if dry_run else 'Deleted'} {failed_count} failed job(s)."
        )

        tmp_root = Path(tempfile.gettempdir())
        removed = 0
        for pattern in ("beatlab-export-*", "beatlab-upload-*"):
            for path in tmp_root.glob(pattern):
                if path.stat().st_mtime < cutoff.timestamp():
                    removed += 1
                    if not dry_run:
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            path.unlink(missing_ok=True)
        self.stdout.write(
            f"{'Would delete' if dry_run else 'Deleted'} {removed} temp item(s)."
        )
