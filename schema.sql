-- Tables for the Wikipedia Time Machine. Created by create_tables.py.

-- One row per article: what Wikipedia shows today. MariaDB keeps every earlier version as a
-- history row. load.py writes those with Wikipedia's real edit times
-- (system_versioning_insert_history), so FOR SYSTEM_TIME AS OF '2015-06-01' means "what
-- Wikipedia showed on 1 June 2015".
CREATE TABLE IF NOT EXISTS article (
  page_id     INT UNSIGNED NOT NULL,          -- Wikipedia's own id
  title       VARCHAR(255) NOT NULL,          -- can change over time: pages get renamed
  rev_id      INT UNSIGNED NOT NULL,          -- Wikipedia's id of this version
  user        VARCHAR(255),                   -- who saved it: user name or IP address
  comment     TEXT,                           -- the edit summary
  minor       BOOL NOT NULL DEFAULT 0,        -- marked as a minor edit
  sha1        CHAR(31),                       -- same hash = same text: how reverts are found
  len         INT UNSIGNED,                   -- length of the text in bytes
  text        MEDIUMTEXT COMPRESSED NOT NULL, -- wikitext, compressed by MariaDB value by value
  row_start   TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
  row_end     TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
  PERIOD FOR SYSTEM_TIME (row_start, row_end),
  PRIMARY KEY (page_id),
  KEY (sha1),
  -- MariaDB's own index for the history is ordered by row_end, so "as of 2005" walks every
  -- version that ended after 2005. This one lets it start from the versions that began before.
  KEY start_key (page_id, row_start)
) CHARACTER SET utf8mb4
  WITH SYSTEM VERSIONING
  -- One partition per year of history. A version goes into the partition of the year it
  -- stopped being current (its row_end); the current versions are in the last one.
  -- AUTO adds a new partition when a year runs out.
  PARTITION BY SYSTEM_TIME INTERVAL 1 YEAR STARTS '2002-01-01 00:00:00' AUTO PARTITIONS 26;

-- The articles to load, from data/articles.tsv.
CREATE TABLE IF NOT EXISTS article_list (
  page_id  INT UNSIGNED NOT NULL PRIMARY KEY,
  title    VARCHAR(255) NOT NULL,
  why      VARCHAR(255)
) CHARACTER SET utf8mb4;
