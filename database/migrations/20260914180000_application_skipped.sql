-- A listing the user passed over (Ctrl+D in `os hunt apply`) leaves the board
-- without counting as an application. `skipped` is a real status so a resync
-- cannot bring the listing back; archiving the row restores it to the board.

alter table app.jobs_applications
  drop constraint jobs_applications_status_check;

alter table app.jobs_applications
  add constraint jobs_applications_status_check
  check (status in (
    'queued', 'applied', 'oa', 'oa_completed', 'interview', 'offer', 'rejected',
    'skipped', 'archived'
  ));
