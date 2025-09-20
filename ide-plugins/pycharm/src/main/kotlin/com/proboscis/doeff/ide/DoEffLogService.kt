package com.proboscis.doeff.ide

import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.PrintWriter
import java.io.StringWriter
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

@Service(Service.Level.PROJECT)
class DoEffLogService(private val project: Project) {
    private val logger = Logger.getInstance(DoEffLogService::class.java)
    private val lock = ReentrantLock()
    private val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")
        .withZone(ZoneId.systemDefault())
    private val logPath: Path = ensureLogPath()

    fun log(entry: DoEffLogEntry) {
        if (entry.level == DoEffLogLevel.ERROR) {
            entry.throwable?.let { logger.error(entry.message, it) } ?: logger.error(entry.message)
        } else {
            logger.info(entry.message)
        }

        val text = buildString {
            append('[')
            append(formatter.format(entry.timestamp))
            append(' ')
            append(entry.level)
            append("] ")
            append(entry.message)
            append('\n')
            entry.details?.takeIf { it.isNotBlank() }?.let {
                append(it.trimEnd())
                append('\n')
            }
            entry.throwable?.let { throwable ->
                append(stackTrace(throwable))
                if (!endsWith("\n")) {
                    append('\n')
                }
            }
        }

        lock.withLock {
            Files.writeString(
                logPath,
                text,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND
            )
        }
    }

    fun log(level: DoEffLogLevel, message: String, details: String? = null, throwable: Throwable? = null) {
        log(DoEffLogEntry(level, message, details, throwable))
    }

    private fun ensureLogPath(): Path {
        val home = Path.of(System.getProperty("user.home"))
        Files.createDirectories(home)
        val target = home.resolve("doeff.log")
        if (!Files.exists(target)) {
            Files.createFile(target)
        }
        return target
    }

    private fun stackTrace(throwable: Throwable): String {
        val buffer = StringWriter()
        PrintWriter(buffer).use { throwable.printStackTrace(it) }
        return buffer.toString()
    }

    companion object {
        fun getInstance(project: Project): DoEffLogService {
            return project.getService(DoEffLogService::class.java)
        }
    }
}
