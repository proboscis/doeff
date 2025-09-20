package com.proboscis.doeff.ide

import com.intellij.codeInsight.daemon.GutterIconNavigationHandler
import com.intellij.codeInsight.daemon.LineMarkerInfo
import com.intellij.codeInsight.daemon.LineMarkerProvider
import com.intellij.icons.AllIcons
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.editor.markup.GutterIconRenderer
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.psi.PsiDocumentManager
import com.intellij.psi.PsiElement
import com.intellij.psi.util.PsiTreeUtil
import com.jetbrains.python.psi.PyClass
import com.jetbrains.python.psi.PyTargetExpression
import java.awt.event.MouseEvent
import java.nio.file.Paths

class ProgramGutterIconProvider : LineMarkerProvider {
    private val logger = Logger.getInstance(ProgramGutterIconProvider::class.java)

    override fun getLineMarkerInfo(element: PsiElement): LineMarkerInfo<*>? {
        val elementType = element.node?.elementType?.toString()
        if (elementType != "Py:IDENTIFIER") {
            return null
        }

        val targetExpression = PsiTreeUtil.getParentOfType(element, PyTargetExpression::class.java)
            ?: return null

        if (targetExpression.nameIdentifier != element) {
            return null
        }

        return buildMarker(targetExpression)
    }

    // Removed collectSlowLineMarkers to prevent duplicate markers
    // The getLineMarkerInfo method is sufficient for our needs

    private class ProgramNavigationHandler(
        private val targetExpression: PyTargetExpression,
        private val typeArgument: String
    ) : GutterIconNavigationHandler<PsiElement> {
        override fun navigate(e: MouseEvent?, element: PsiElement?) {
            FileDocumentManager.getInstance().saveAllDocuments()
            ProgramExecutionController.handleNavigation(e, targetExpression, typeArgument)
        }
    }

    private fun logSkip(targetExpression: PyTargetExpression, reason: String) {
        val location = location(targetExpression)
        logger.info(
            "Program gutter: skipping ${targetExpression.name} at $location because $reason"
        )
        if (reason.contains("Program")) {
            ProgramPluginDiagnostics.info(
                targetExpression.project,
                "Skipped doeff gutter for ${targetExpression.name}: $reason",
                key = "skip-$location-${targetExpression.name}"
            )
        }
    }

    private fun location(targetExpression: PyTargetExpression): String {
        val psiFile = targetExpression.containingFile
        val projectBasePath = targetExpression.project.basePath
        val rawPath = psiFile?.virtualFile?.path ?: "<unknown>"
        val displayPath = if (projectBasePath != null && rawPath != "<unknown>") {
            val base = try {
                Paths.get(projectBasePath)
            } catch (_: Exception) {
                null
            }
            val absolute = try {
                Paths.get(rawPath)
            } catch (_: Exception) {
                null
            }
            if (base != null && absolute != null && absolute.isAbsolute) {
                try {
                    base.relativize(absolute).toString()
                } catch (_: Exception) {
                    absolute.toString()
                }
            } else {
                rawPath
            }
        } else {
            rawPath
        }
        val document = psiFile?.let {
            PsiDocumentManager.getInstance(targetExpression.project).getDocument(it)
        }
        val line = document?.getLineNumber(targetExpression.textOffset)?.plus(1) ?: -1
        return if (line >= 0) "$displayPath:$line" else displayPath
    }

    private fun buildMarker(targetExpression: PyTargetExpression): LineMarkerInfo<*>? {
        val identifier = targetExpression.nameIdentifier ?: return null

        val targetLocation = location(targetExpression)

        logger.debug(
            "Program gutter: inspecting ${targetExpression.name} ($targetLocation) with annotation '${targetExpression.annotation?.text}'"
        )

        if (PsiTreeUtil.getParentOfType(targetExpression, PyClass::class.java) != null) {
            logSkip(targetExpression, "defined inside a class")
            return null
        }

        val annotation = targetExpression.annotation ?: run {
            logSkip(targetExpression, "missing annotation")
            return null
        }

        val extractedType = ProgramTypeExtractor.extractProgramType(annotation)
        val typeArgument = when {
            extractedType != null -> extractedType.typeArgument
            annotation.text.contains("Program") -> {
                val message =
                    "Program gutter: unable to parse Program annotation '${annotation.text}' for ${targetExpression.name} at $targetLocation"
                logger.warn(message)
                ProgramPluginDiagnostics.warn(
                    targetExpression.project,
                    message,
                    key = "parse-$targetLocation"
                )
                "Any"
            }
            else -> {
                logSkip(targetExpression, "annotation '${annotation.text}' is not Program")
                return null
            }
        }

        val addedMessage =
            "Program gutter added for ${targetExpression.name} with type $typeArgument at $targetLocation"
        logger.info(addedMessage)
        ProgramPluginDiagnostics.info(
            targetExpression.project,
            addedMessage,
            key = "added-$targetLocation-${targetExpression.name}"
        )

        return LineMarkerInfo(
            identifier,
            identifier.textRange,
            AllIcons.RunConfigurations.TestState.Run,
            { "Run doeff Program[$typeArgument]" },
            ProgramNavigationHandler(targetExpression, typeArgument),
            GutterIconRenderer.Alignment.LEFT,
            { "Run doeff Program[$typeArgument]" }
        )
    }
}
