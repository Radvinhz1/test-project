-- Run in SSMS while connected to (localdb)\MSSQLLocalDB.
-- Creates a separate database; existing application databases are untouched.
USE [master];
GO
IF DB_ID(N'TelegramUploader') IS NULL
BEGIN
    CREATE DATABASE [TelegramUploader];
END;
GO
