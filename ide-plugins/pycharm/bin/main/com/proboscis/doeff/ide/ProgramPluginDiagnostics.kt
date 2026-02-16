package com.proboscis.doeff.ide

import com.intellij.diagnostic.LoadingState
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.StartupManager
import com.proboscis.doeff.ide.DoEffLogLevel
import java.util.concurrent.ConcurrentHashMap

object ProgramPluginDiagnostics {
    private val logger = Logger.getInstance(ProgramPluginDiagnostics::class.java)
    private val notifiedKeys = ConcurrentHashMap.newKeySet<String>()

    fun info(project: Project, message: String, key: String? = null) {
        logToFile(project, DoEffLogLevel.INFO, message)
        logger.info(message)
        notify(project, message, NotificationType.INFORMATION, key)
    }

    fun warn(project: Project, message: String, key: String? = null) {
        logToFile(project, DoEffLogLevel.WARN, message)
        logger.warn(message)
        notify(project, message, NotificationType.WARNING, key)
    }

    fun error(project: Project, message: String, throwable: Throwable? = null, key: String? = null) {
        logToFile(project, DoEffLogLevel.ERROR, message, throwable = throwable)
        throwable?.let { logger.error(message, it) } ?: logger.error(message)
        notify(project, message, NotificationType.ERROR, key)
    }

    private fun notify(project: Project, message: String, type: NotificationType, key: String?) {
        if (key != null && !notifiedKeys.add("$type::$key")) {
            return
        }

        if (!LoadingState.COMPONENTS_LOADED.isOccurred) {
            StartupManager.getInstance(project).runAfterOpened {
                doNotify(project, message, type)
            }
            return
        }

        doNotify(project, message, type)
    }

    private fun doNotify(project: Project, message: String, type: NotificationType) {
        ApplicationManager.getApplication().invokeLater {
            NotificationGroupManager.getInstance()
                .getNotificationGroup("doeff.plugin")
                .createNotification("doeff", message, type)
                .notify(project)
        }
    }

    private fun logToFile(
        project: Project,
        level: DoEffLogLevel,
        message: String,
        details: String? = null,
        throwable: Throwable? = null
    ) {
        DoEffLogService.getInstance(project).log(level, message, details, throwable)
    }
}
