package com.proboscis.doeff.ide

import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.StartupActivity
import com.intellij.openapi.startup.StartupManager
import org.apache.log4j.Level

class ProgramPluginStartup : StartupActivity.DumbAware {
    private val logger = Logger.getInstance(ProgramPluginStartup::class.java)

    override fun runActivity(project: Project) {
        logger.info("doeff PyCharm plugin startup scheduled for project ${project.name}")
        StartupManager.getInstance(project).runAfterOpened {
            configureLogging()
            DoEffLogService.getInstance(project).log(
                DoEffLogLevel.INFO,
                "doeff PyCharm plugin lifecycle started for project ${project.name}"
            )
            logger.info("doeff PyCharm plugin startup notifications for project ${project.name}")
            ProgramPluginDiagnostics.info(
                project,
                message = "doeff PyCharm plugin is active.",
                key = "startup-${project.locationHash}"
            )
        }
    }

    private fun configureLogging() {
        val categories = listOf(
            "com.proboscis.doeff.ide",
            ProgramGutterIconProvider::class.java.name,
            ProgramExecutionController::class.java.name,
            ProgramTypeExtractor::class.java.name,
            IndexerClient::class.java.name,
            ProgramSelectionDialog::class.java.name,
            DoEffRunConfigurationHelper::class.java.name,
            ProgramPluginDiagnostics::class.java.name
        )

        categories.forEach { category ->
            Logger.getInstance(category).setLevel(Level.INFO)
        }
    }
}
