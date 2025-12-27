-- Database Initialization
CREATE DATABASE IF NOT EXISTS `data-analysis`
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE `data-analysis`;

-- Mailbox table (PST files)
CREATE TABLE IF NOT EXISTS `mailbox`
(
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `owner_identifier` VARCHAR(255) NOT NULL COMMENT 'Owner identifier (e.g. filename stem)',
    `pst_filename` VARCHAR(512) NOT NULL UNIQUE COMMENT 'Absolute path to the source PST',
    `imported_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_pst_filename` (`pst_filename`)
    ) ENGINE=InnoDB;

-- Table des identités uniques (emails normalisés)
CREATE TABLE IF NOT EXISTS `identity` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `email_address` VARCHAR(255) NOT NULL UNIQUE,
    INDEX `idx_email` (`email_address`)
) ENGINE=InnoDB;

-- Table des emails (Deduplicated)
CREATE TABLE IF NOT EXISTS `email` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
    `message_fingerprint` CHAR(64) NOT NULL UNIQUE,
    `subject` TEXT DEFAULT NULL,
    `sender_identity_id` INT DEFAULT NULL, -- Référence à identity
    `sent_at` DATETIME DEFAULT NULL,
    `raw_body` LONGTEXT DEFAULT NULL,
    `normalized_body` LONGTEXT DEFAULT NULL,
    `has_attachments` BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (`sender_identity_id`) REFERENCES `identity`(`id`),
    INDEX `idx_fingerprint` (`message_fingerprint`)
) ENGINE=InnoDB;

-- Table de jointure N:M pour les destinataires
CREATE TABLE IF NOT EXISTS `email_recipient` (
    `email_id` BIGINT NOT NULL,
    `identity_id` INT NOT NULL,
    `type` ENUM('TO', 'CC', 'BCC') DEFAULT 'TO' NOT NULL,
    PRIMARY KEY (`email_id`, `identity_id`, `type`),
    FOREIGN KEY (`email_id`) REFERENCES `email`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`identity_id`) REFERENCES `identity`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Junction table: Email / Mailbox (Traceability)
CREATE TABLE IF NOT EXISTS `email_mailbox`
(
    `email_id` BIGINT NOT NULL,
    `mailbox_id` INT NOT NULL,
    PRIMARY KEY (`email_id`, `mailbox_id`),
    FOREIGN KEY (`email_id`) REFERENCES `email`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`mailbox_id`) REFERENCES `mailbox`(`id`) ON DELETE CASCADE
    ) ENGINE=InnoDB;

-- Attachments table
CREATE TABLE IF NOT EXISTS `attachment`
(
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
    `email_id` BIGINT NOT NULL,
    `filename` VARCHAR(255) NOT NULL,
    `size` INT NOT NULL COMMENT 'Size in bytes',
    `storage_path` VARCHAR(1024) NOT NULL,
    FOREIGN KEY (`email_id`) REFERENCES `email`(`id`) ON DELETE CASCADE,
    INDEX `idx_attachment_email` (`email_id`)
    ) ENGINE=InnoDB;