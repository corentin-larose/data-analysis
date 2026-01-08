create table `data-analysis`.email_addresses
(
    id    int auto_increment
        primary key,
    email varchar(255) not null,
    constraint email
        unique (email)
)
    collate = utf8mb4_unicode_ci;

create table `data-analysis`.identity
(
    id            int auto_increment
        primary key,
    email_address varchar(255) not null,
    name          varchar(255) null,
    constraint email_address
        unique (email_address)
);

create table `data-analysis`.email
(
    id                  bigint auto_increment
        primary key,
    message_fingerprint char(64)                     not null,
    subject             text                         null,
    sender_identity_id  int                          null,
    sent_at             datetime                     null,
    raw_body            longtext                     null,
    normalized_body     longtext                     null,
    cleaned_body        longtext                     null,
    has_attachments     tinyint(1) default 0         null,
    message_id          varchar(255)                 null,
    in_reply_to         varchar(255)                 null,
    thread_references   text                         null,
    tone_flags          longtext collate utf8mb4_bin null
        check (json_valid(`tone_flags`)),
    constraint message_fingerprint
        unique (message_fingerprint),
    constraint email_ibfk_1
        foreign key (sender_identity_id) references `data-analysis`.identity (id)
);

create table `data-analysis`.attachment
(
    id                 bigint auto_increment
        primary key,
    email_id           bigint        not null,
    filename           varchar(255)  not null,
    size               int           not null comment 'Size in bytes',
    storage_path       varchar(1024) not null,
    extension          varchar(20)   null,
    mime_type_declared varchar(100)  null,
    constraint attachment_ibfk_1
        foreign key (email_id) references `data-analysis`.email (id)
            on delete cascade
);

create index idx_attachment_email
    on `data-analysis`.attachment (email_id);

create index idx_fingerprint
    on `data-analysis`.email (message_fingerprint);

create index idx_msg_id
    on `data-analysis`.email (message_id);

create index sender_identity_id
    on `data-analysis`.email (sender_identity_id);

create table `data-analysis`.email_recipient
(
    email_id    bigint                                not null,
    identity_id int                                   not null,
    type        enum ('TO', 'CC', 'BCC') default 'TO' not null,
    primary key (email_id, identity_id, type),
    constraint email_recipient_ibfk_1
        foreign key (email_id) references `data-analysis`.email (id)
            on delete cascade,
    constraint email_recipient_ibfk_2
        foreign key (identity_id) references `data-analysis`.identity (id)
            on delete cascade
);

create index identity_id
    on `data-analysis`.email_recipient (identity_id);

create index idx_email
    on `data-analysis`.identity (email_address);

create index idx_name
    on `data-analysis`.identity (name);

create table `data-analysis`.mailbox
(
    id               int auto_increment
        primary key,
    owner_identifier varchar(255)                         not null comment 'Owner identifier (e.g. filename stem)',
    pst_filename     varchar(512)                         not null comment 'Absolute path to the source PST',
    imported_at      datetime default current_timestamp() not null,
    constraint pst_filename
        unique (pst_filename)
);

create table `data-analysis`.email_mailbox
(
    email_id    bigint       not null,
    mailbox_id  int          not null,
    folder_path varchar(512) null,
    primary key (email_id, mailbox_id),
    constraint email_mailbox_ibfk_1
        foreign key (email_id) references `data-analysis`.email (id)
            on delete cascade,
    constraint email_mailbox_ibfk_2
        foreign key (mailbox_id) references `data-analysis`.mailbox (id)
            on delete cascade
);

create index mailbox_id
    on `data-analysis`.email_mailbox (mailbox_id);

create index idx_pst_filename
    on `data-analysis`.mailbox (pst_filename);

