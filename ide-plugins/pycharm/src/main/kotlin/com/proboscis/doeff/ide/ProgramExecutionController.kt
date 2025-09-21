package com.proboscis.doeff.ide

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.wm.StatusBar
import com.intellij.openapi.wm.WindowManager
import com.jetbrains.python.psi.PyFile
import com.jetbrains.python.psi.PyTargetExpression
import java.awt.event.MouseEvent

object ProgramExecutionController {
    private val logger = Logger.getInstance(ProgramExecutionController::class.java)

    fun handleNavigation(mouseEvent: MouseEvent?, targetExpression: PyTargetExpression, typeArgument: String) {
        val project = targetExpression.project
        updateStatusBar(project, "Doeff: Analyzing ${targetExpression.name}...")
        
        val modulePath = findModulePath(targetExpression)
        if (modulePath == null) {
            logger.warn("Unable to determine module path for ${targetExpression.text}")
            ProgramPluginDiagnostics.error(
                project,
                "Unable to determine module path for ${targetExpression.text}"
            )
            showErrorPopup(project, "Doeff Navigation Error", "Unable to determine module path for ${targetExpression.text}")
            updateStatusBar(project, "Doeff: Error - Module path not found")
            return
        }

        val programPath = "$modulePath.${targetExpression.name}"
        logger.debug("Program gutter navigation for $programPath with type $typeArgument")
        ProgramPluginDiagnostics.info(
            project,
            "Opening doeff runner for $programPath (type $typeArgument)",
            key = "nav-$programPath"
        )
        
        updateStatusBar(project, "Doeff: Indexing project for $programPath...")
        showInfoNotification(project, "Doeff Indexing", "Searching for interpreters and transformers...")
        
        val indexer = IndexerClient(project)
        // Use the find-interpreters command to get only valid interpreters
        indexer.findInterpreters(typeArgument) { interpreters ->
            if (interpreters.isEmpty()) {
                logger.warn("No interpreters found in index for type $typeArgument")
                ProgramPluginDiagnostics.warn(
                    project,
                    "No interpreters found for doeff type $typeArgument",
                    key = "no-interpreter-$typeArgument"
                )
                showErrorPopup(project, "No Interpreters Found", 
                    "No doeff interpreters found for type '$typeArgument'.\n" +
                    "Make sure you have functions that:\n" +
                    "- Accept a Program parameter, or\n" +
                    "- Are marked with # doeff: interpreter")
                updateStatusBar(project, "Doeff: No interpreters found")
                return@findInterpreters
            }

            // Get Kleisli functions using the dedicated command
            indexer.findKleisli(typeArgument) { kleisli ->
                // Get transformers using the dedicated command
                indexer.findTransforms(typeArgument) { transformers ->
                    updateStatusBar(project, "Doeff: Found ${interpreters.size} interpreters, ${kleisli.size} kleisli, ${transformers.size} transformers")
                    
                    val dialog = ProgramSelectionDialog(
                        project = project,
                        programPath = programPath,
                        programType = typeArgument,
                        interpreters = interpreters,
                        kleisliPrograms = kleisli,
                        transformers = transformers
                    )

                    if (dialog.showAndGet()) {
                        val selection = dialog.buildSelection()
                        if (selection != null) {
                            logger.debug("Launching run configuration for ${selection.programPath}")
                            val interpreterName = selection.interpreter.qualifiedName
                            ProgramPluginDiagnostics.info(
                                project,
                                "Launching doeff run for ${selection.programPath} with interpreter $interpreterName",
                                key = "run-${selection.programPath}-$interpreterName"
                            )
                            updateStatusBar(project, "Doeff: Launching ${selection.programPath}...")
                            DoEffRunConfigurationHelper(project).run(selection)
                        } else {
                            logger.warn("Dialog returned no selection")
                            ProgramPluginDiagnostics.warn(
                                project,
                                "doeff run dialog closed without a selection for $programPath",
                                key = "no-selection-$programPath"
                            )
                            updateStatusBar(project, "Doeff: Cancelled")
                        }
                    }
                }
            }
        }
    }

    private fun usageMatchesType(entry: IndexEntry, typeArgument: String): Boolean {
        if (typeArgument.equals("Any", ignoreCase = true)) {
            return true
        }
        if (entry.programParameters.isNotEmpty()) {
            entry.programParameters[0].annotation?.let {
                if (it.contains(typeArgument)) {
                    return true
                }
            }
        }
        return entry.typeUsages.any { it.matchesType(typeArgument) }
    }

    private fun findModulePath(targetExpression: PyTargetExpression): String? {
        val pyFile = targetExpression.containingFile as? PyFile ?: return null
        val virtualFile = pyFile.virtualFile ?: return null
        val basePath = targetExpression.project.basePath ?: return null
        val baseVirtualFile = VfsUtil.findFileByIoFile(java.io.File(basePath), true) ?: return null
        val relativePath = VfsUtil.getRelativePath(virtualFile, baseVirtualFile) ?: return null
        val withoutExtension = relativePath.removeSuffix(".py")
        return withoutExtension.replace("/", ".").replace("\\", ".")
    }

    private fun notify(project: Project, message: String, type: NotificationType) {
        ApplicationManager.getApplication().invokeLater {
            NotificationGroupManager.getInstance()
                .getNotificationGroup("doeff.plugin")
                .createNotification("doeff", message, type)
                .notify(project)
        }
    }
    
    private fun showErrorPopup(project: Project, title: String, message: String) {
        ApplicationManager.getApplication().invokeLater {
            NotificationGroupManager.getInstance()
                .getNotificationGroup("doeff.plugin")
                .createNotification(title, message, NotificationType.ERROR)
                .notify(project)
        }
    }
    
    private fun showInfoNotification(project: Project, title: String, message: String) {
        ApplicationManager.getApplication().invokeLater {
            NotificationGroupManager.getInstance()
                .getNotificationGroup("doeff.plugin")
                .createNotification(title, message, NotificationType.INFORMATION)
                .notify(project)
        }
    }
    
    private fun updateStatusBar(project: Project, message: String) {
        ApplicationManager.getApplication().invokeLater {
            val statusBar = WindowManager.getInstance().getStatusBar(project)
            statusBar?.info = message
            // Clear the message after 5 seconds
            ApplicationManager.getApplication().executeOnPooledThread {
                Thread.sleep(5000)
                ApplicationManager.getApplication().invokeLater {
                    if (statusBar?.info == message) {
                        statusBar.info = ""
                    }
                }
            }
        }
    }
}
