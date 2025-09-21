package com.proboscis.doeff.ide

import com.google.gson.Gson
import com.google.gson.JsonSyntaxException
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

class IndexerClient(private val project: Project) {
    private val log = Logger.getInstance(IndexerClient::class.java)
    private val gson = Gson()

    fun queryEntries(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexer(typeArgument) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to query doeff-indexer", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while querying doeff-indexer", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findInterpreters(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-interpreters", typeArgument) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find interpreters", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding interpreters", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findTransforms(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-transforms", typeArgument) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find transforms", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding transforms", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findKleisli(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-kleisli", typeArgument) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find Kleisli functions", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding Kleisli functions", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }

    private fun runIndexerCommand(command: String, typeArgument: String?): List<IndexEntry>? {
        val indexerPath = locateIndexer() ?: return emptyList()
        val root = project.basePath ?: return emptyList()
        val commandList = mutableListOf(indexerPath, command, "--root", root)
        if (!typeArgument.isNullOrBlank()) {
            commandList.add("--type-arg")
            commandList.add(typeArgument)
        }

        log.debug("Executing doeff-indexer: ${commandList.joinToString(" ")}")
        val process = ProcessBuilder(commandList)
            .directory(File(root))
            .start()

        // Read output and error streams concurrently to avoid deadlock
        val output = StringBuilder()
        val error = StringBuilder()
        
        // Create threads to read streams
        val outputReader = Thread {
            process.inputStream.bufferedReader().use { reader ->
                output.append(reader.readText())
            }
        }
        val errorReader = Thread {
            process.errorStream.bufferedReader().use { reader ->
                error.append(reader.readText())
            }
        }
        
        // Start reading streams
        outputReader.start()
        errorReader.start()
        
        // Wait for process to complete
        val completed = process.waitFor(30, TimeUnit.SECONDS)
        
        // Wait for readers to finish
        outputReader.join(1000)
        errorReader.join(1000)
        
        if (!completed) {
            process.destroyForcibly()
            notifyError("doeff-indexer timed out", "Waited 30 seconds without response", null)
            return emptyList()
        }

        if (process.exitValue() != 0) {
            val errorText = error.toString()
            notifyError("doeff-indexer failed", errorText.ifBlank { "Exit code ${process.exitValue()}" }, null)
            return emptyList()
        }

        return parseEntries(output.toString())
    }
    
    private fun runIndexer(typeArgument: String?): List<IndexEntry>? {
        val indexerPath = locateIndexer() ?: return emptyList()
        val root = project.basePath ?: return emptyList()
        val command = mutableListOf(indexerPath, "--root", root, "--kind", "any")
        if (!typeArgument.isNullOrBlank()) {
            command.add("--type-arg")
            command.add(typeArgument)
        }

        log.debug("Executing doeff-indexer: ${command.joinToString(" ")}")
        val process = ProcessBuilder(command)
            .directory(File(root))
            .start()

        // Read output and error streams concurrently to avoid deadlock
        val output = StringBuilder()
        val error = StringBuilder()
        
        // Create threads to read streams
        val outputReader = Thread {
            process.inputStream.bufferedReader().use { reader ->
                output.append(reader.readText())
            }
        }
        val errorReader = Thread {
            process.errorStream.bufferedReader().use { reader ->
                error.append(reader.readText())
            }
        }
        
        // Start reading streams
        outputReader.start()
        errorReader.start()
        
        // Wait for process to complete
        val completed = process.waitFor(30, TimeUnit.SECONDS)
        
        // Wait for readers to finish
        outputReader.join(1000)
        errorReader.join(1000)
        
        if (!completed) {
            process.destroyForcibly()
            notifyError("doeff-indexer timed out", "Waited 30 seconds without response", null)
            return emptyList()
        }

        if (process.exitValue() != 0) {
            val errorText = error.toString()
            notifyError("doeff-indexer failed", errorText.ifBlank { "Exit code ${process.exitValue()}" }, null)
            return emptyList()
        }

        return parseEntries(output.toString())
    }

    private fun parseEntries(output: String): List<IndexEntry>? {
        return try {
            val payload = gson.fromJson(output, IndexPayload::class.java)
            payload.entries
        } catch (ex: JsonSyntaxException) {
            notifyError("Invalid indexer output", ex.message ?: "Unable to parse JSON", ex)
            log.warn("Failed to parse indexer output: $output", ex)
            null
        }
    }

    private fun locateIndexer(): String? {
        System.getenv("DOEFF_INDEXER_PATH")?.takeIf { it.isNotBlank() }?.let { path ->
            val file = File(path)
            if (file.canExecute()) {
                return file.absolutePath
            }
        }

        val candidates = listOf(
            "/usr/local/bin/doeff-indexer",
            "/usr/bin/doeff-indexer",
            "${System.getProperty("user.home")}/.cargo/bin/doeff-indexer",
            "${System.getProperty("user.home")}/.local/bin/doeff-indexer",
            "/opt/homebrew/bin/doeff-indexer"
        )

        return candidates.firstOrNull { File(it).canExecute() }
            ?: run {
                notifyError(
                    "doeff-indexer not found",
                    "Install with 'cargo install --path packages/doeff-indexer' or set DOEFF_INDEXER_PATH",
                    null
                )
                null
            }
    }

    private fun notifyError(title: String, message: String, throwable: Throwable?) {
        ProgramPluginDiagnostics.error(
            project,
            "$title: $message",
            throwable = throwable,
            key = "indexer-error-$title"
        )
    }
}
