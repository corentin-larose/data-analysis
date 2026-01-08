-- Disable foreign key checks to allow truncating tables
SET FOREIGN_KEY_CHECKS = 0;

-- Empty all tables and reset auto-increment counters
TRUNCATE TABLE `attachment`;
TRUNCATE TABLE `email`;
TRUNCATE TABLE `email_mailbox`;
TRUNCATE TABLE `email_recipient`;
TRUNCATE TABLE `identity`;
TRUNCATE TABLE `mailbox`;

-- Re-enable foreign key checks
SET FOREIGN_KEY_CHECKS = 1;

-- Optional: Verify that tables are empty
SELECT 'mailbox' as table_name, COUNT(*) as row_count FROM mailbox
UNION
SELECT 'email', COUNT(*) FROM email
UNION
SELECT 'email_recipient', COUNT(*) FROM email_recipient
UNION
SELECT 'attachment', COUNT(*) FROM attachment
UNION
SELECT 'email_mailbox', COUNT(*) FROM email_mailbox;
