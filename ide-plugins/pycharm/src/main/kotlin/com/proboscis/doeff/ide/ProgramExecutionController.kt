package com.proboscis.doeff.ide

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VfsUtil
import com.jetbrains.python.psi.PyFile
import com.jetbrains.python.psi.PyTargetExpression
import java.awt.event.MouseEvent

object ProgramExecutionController {
    private val logger = Logger.getInstance(ProgramExecutionController::class.java)

    fun handleNavigation(mouseEvent: MouseEvent?, targetExpression: PyTargetExpression, typeArgument: String) {
        val project = targetExpression.project
        val modulePath = findModulePath(targetExpression)
        if (modulePath == null) {
            logger.warn("Unable to determine module path for ${targetExpression.text}")
            ProgramPluginDiagnostics.error(
                project,
                "Unable to determine module path for ${targetExpression.text}"
            )
            notify(project, "Unable to determine module path", NotificationType.WARNING)
            return
        }

        val programPath = "$modulePath.${targetExpression.name}"
        logger.debug("Program gutter navigation for $programPath with type $typeArgument")
        ProgramPluginDiagnostics.info(
            project,
            "Opening doeff runner for $programPath (type $typeArgument)",
            key = "nav-$programPath"
        )
        val indexer = IndexerClient(project)
        indexer.queryEntries(typeArgument) { entries ->
            // First try to filter by marker "interpreter" if any are marked
            val markedInterpreters = entries.filter { it.hasMarker("interpreter") }
            // Fall back to category-based detection if no markers found
            val interpreters = if (markedInterpreters.isNotEmpty()) {
                markedInterpreters
            } else {
                entries.filter { it.hasCategory(IndexEntryCategory.PROGRAM_INTERPRETER) }
            }
            if (interpreters.isEmpty()) {
                logger.warn("No interpreters found in index for type $typeArgument")
                ProgramPluginDiagnostics.warn(
                    project,
                    "No interpreters found for doeff type $typeArgument",
                    key = "no-interpreter-$typeArgument"
                )
                notify(project, "No doeff interpreters found", NotificationType.WARNING)
                return@queryEntries
            }

            // For Kleisli programs, check markers first
            val markedKleisli = entries.filter { it.hasMarker("kleisli") }
                .filter { usageMatchesType(it, typeArgument) }
            val kleisli = if (markedKleisli.isNotEmpty()) {
                markedKleisli
            } else {
                entries.filter { it.hasCategory(IndexEntryCategory.KLEISLI_PROGRAM) }
                    .filter { usageMatchesType(it, typeArgument) }
            }
            
            // For transformers, check markers first
            val markedTransformers = entries.filter { it.hasMarker("transform") || it.hasMarker("transformer") }
            val transformers = if (markedTransformers.isNotEmpty()) {
                markedTransformers
            } else {
                entries.filter { it.hasCategory(IndexEntryCategory.PROGRAM_TRANSFORMER) }
            }

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
                    DoEffRunConfigurationHelper(project).run(selection)
                } else {
                    logger.warn("Dialog returned no selection")
                    ProgramPluginDiagnostics.warn(
                        project,
                        "doeff run dialog closed without a selection for $programPath",
                        key = "no-selection-$programPath"
                    )
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
}
