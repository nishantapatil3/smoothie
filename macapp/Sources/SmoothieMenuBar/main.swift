import AVFoundation
import AppKit
import Combine
import Foundation
import Security
import SwiftUI
import UniformTypeIdentifiers

// MARK: - API models

struct AuthTokens: Codable {
    let token: String
    let refreshToken: String
}

struct User: Codable {
    let id: String
    let username: String
    let tier: String
}

struct Recording: Codable, Identifiable {
    let id: String
    let title: String?
    let generatedTitle: String?
    let status: String
    let durationSec: Double?
    let createdAt: Date
    let progress: Double
}

struct APIErrorResponse: Codable {
    let detail: String?
}

enum SmoothieError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let message): return message
        }
    }
}

// MARK: - Keychain

enum TokenStore {
    private static let service = "com.smoothie.menu-bar"
    private static let account = "auth-tokens"

    static func load() -> AuthTokens? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return try? JSONDecoder().decode(AuthTokens.self, from: data)
    }

    static func save(_ tokens: AuthTokens) {
        guard let data = try? JSONEncoder().encode(tokens) else { return }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            SecItemAdd(item as CFDictionary, nil)
        }
    }

    static func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

// MARK: - Backend API

@MainActor
final class SmoothieAPI {
    private let baseURL = URL(string: "http://127.0.0.1:5173/api")!
    private var tokens: AuthTokens?
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init() {
        tokens = TokenStore.load()
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
    }

    var isAuthenticated: Bool { tokens != nil }

    func login(username: String, password: String, register: Bool) async throws -> User {
        let endpoint = register ? "auth/register" : "auth/login"
        let body = try encoder.encode(["username": username, "password": password])
        let data = try await request(endpoint, method: "POST", body: body, authenticated: false)
        let newTokens = try decoder.decode(AuthTokens.self, from: data)
        tokens = newTokens
        TokenStore.save(newTokens)
        return try await me()
    }

    func me() async throws -> User {
        let data = try await request("auth/me")
        return try decoder.decode(User.self, from: data)
    }

    func logout() async {
        if let tokens {
            let body = try? encoder.encode(["refresh_token": tokens.refreshToken])
            _ = try? await request("auth/logout", method: "POST", body: body, authenticated: true)
        }
        tokens = nil
        TokenStore.clear()
    }

    func recordings() async throws -> [Recording] {
        let data = try await request("recordings")
        return try decoder.decode([Recording].self, from: data)
    }

    func upload(fileURL: URL, title: String) async throws -> Recording {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        let fileData = try Data(contentsOf: fileURL)
        let contentType = contentType(for: fileURL)

        body.append(string: "--\(boundary)\r\n")
        body.append(string: "Content-Disposition: form-data; name=\"audio\"; filename=\"\(fileURL.lastPathComponent)\"\r\n")
        body.append(string: "Content-Type: \(contentType)\r\n\r\n")
        body.append(fileData)
        body.append(string: "\r\n--\(boundary)\r\n")
        body.append(string: "Content-Disposition: form-data; name=\"title\"\r\n\r\n\(title)\r\n")
        body.append(string: "--\(boundary)--\r\n")

        let data = try await request(
            "recordings",
            method: "POST",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)",
            authenticated: true
        )
        return try decoder.decode(Recording.self, from: data)
    }

    private func contentType(for url: URL) -> String {
        if let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType {
            return type
        }
        return "audio/mpeg"
    }

    private func request(
        _ path: String,
        method: String = "GET",
        body: Data? = nil,
        contentType: String = "application/json",
        authenticated: Bool = true,
        retrying: Bool = false
    ) async throws -> Data {
        var urlRequest = URLRequest(url: baseURL.appendingPathComponent(path))
        urlRequest.httpMethod = method
        urlRequest.httpBody = body
        if body != nil { urlRequest.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        if authenticated, let token = tokens?.token {
            urlRequest.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response) = try await URLSession.shared.data(for: urlRequest)
        guard let http = response as? HTTPURLResponse else {
            throw SmoothieError.message("The backend returned an invalid response.")
        }

        if http.statusCode == 401, authenticated, !retrying, tokens != nil {
            try await refresh()
            return try await request(
                path,
                method: method,
                body: body,
                contentType: contentType,
                authenticated: authenticated,
                retrying: true
            )
        }

        guard (200..<300).contains(http.statusCode) else {
            let apiError = try? decoder.decode(APIErrorResponse.self, from: data)
            throw SmoothieError.message(apiError?.detail ?? "Backend request failed (HTTP \(http.statusCode)).")
        }
        return data
    }

    private func refresh() async throws {
        guard let refreshToken = tokens?.refreshToken else {
            throw SmoothieError.message("Your session expired. Please sign in again.")
        }
        let body = try encoder.encode(["refresh_token": refreshToken])
        let data = try await request("auth/refresh", method: "POST", body: body, authenticated: false)
        let newTokens = try decoder.decode(AuthTokens.self, from: data)
        tokens = newTokens
        TokenStore.save(newTokens)
    }
}

private extension Data {
    mutating func append(string: String) {
        append(string.data(using: .utf8)!)
    }
}

// MARK: - Backend process

@MainActor
final class BackendProcessManager {
    private var process: Process?

    func startIfNeeded() async -> Bool {
        if await isHealthy() { return true }
        guard let backendRoot = findBackendRoot(), let python = findPython(in: backendRoot) else {
            return false
        }

        let backend = Process()
        backend.executableURL = URL(fileURLWithPath: python)
        backend.arguments = ["smoothie/main.py"]
        backend.currentDirectoryURL = backendRoot
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["HF_HUB_DISABLE_XET"] = "1"
        backend.environment = environment
        backend.standardOutput = FileHandle.nullDevice
        backend.standardError = FileHandle.nullDevice

        do {
            try backend.run()
            process = backend
        } catch {
            return false
        }

        for _ in 0..<90 {
            if await isHealthy() { return true }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        return false
    }

    func stop() {
        guard let process, process.isRunning else { return }
        process.terminate()
        self.process = nil
    }

    private func isHealthy() async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:5173/api/health") else { return false }
        do {
            let (_, response) = try await URLSession.shared.data(from: url)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    private func findBackendRoot() -> URL? {
        var current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        for _ in 0..<5 {
            let candidate = current.appendingPathComponent("backend")
            if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("smoothie/main.py").path) {
                return candidate
            }
            current.deleteLastPathComponent()
        }

        guard let bundled = Bundle.main.resourceURL?.appendingPathComponent("backend"),
              FileManager.default.fileExists(atPath: bundled.appendingPathComponent("smoothie/main.py").path) else {
            return nil
        }

        // Application bundles are read-only. Keep the daemon, database, audio,
        // and editable config under Application Support instead.
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Smoothie", isDirectory: true)
        let destination = support.appendingPathComponent("backend", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.copyItem(at: bundled, to: destination)
            }
            if let bundledWebUI = Bundle.main.resourceURL?.appendingPathComponent("webui"),
               FileManager.default.fileExists(atPath: bundledWebUI.path) {
                let destinationWebUI = support.appendingPathComponent("webui", isDirectory: true)
                if !FileManager.default.fileExists(atPath: destinationWebUI.path) {
                    try FileManager.default.copyItem(at: bundledWebUI, to: destinationWebUI)
                }
            }
            return destination
        } catch {
            return nil
        }
    }

    private func findPython(in backendRoot: URL) -> String? {
        let candidates = [
            backendRoot.appendingPathComponent(".venv/bin/python").path,
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }
}

// MARK: - Recording

@MainActor
final class AudioRecorder: NSObject {
    private var recorder: AVAudioRecorder?
    private var outputURL: URL?

    var isRecording: Bool { recorder?.isRecording == true }

    func start() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("smoothie-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        let newRecorder = try AVAudioRecorder(url: url, settings: settings)
        guard newRecorder.record() else {
            throw SmoothieError.message("macOS could not start microphone recording.")
        }
        recorder = newRecorder
        outputURL = url
    }

    func stop() -> URL? {
        recorder?.stop()
        recorder = nil
        let url = outputURL
        outputURL = nil
        return url
    }
}

// MARK: - Application model

@MainActor
final class AppModel: ObservableObject {
    @Published var backendOnline = false
    @Published var user: User?
    @Published var recordings: [Recording] = []
    @Published var isRecording = false
    @Published var isBusy = false
    @Published var message = "Starting local backend…"

    let api = SmoothieAPI()
    private let backend = BackendProcessManager()
    private let recorder = AudioRecorder()
    private var refreshTask: Task<Void, Never>?

    init() {
        Task { await bootstrap() }
    }

    deinit {
        refreshTask?.cancel()
    }

    var isAuthenticated: Bool { user != nil }

    func bootstrap() async {
        backendOnline = await backend.startIfNeeded()
        guard backendOnline else {
            message = "Backend is not available. Build or install the bundled backend."
            return
        }
        if api.isAuthenticated {
            do {
                user = try await api.me()
                message = "Ready"
                startRefreshing()
                await refreshRecordings()
            } catch {
                await api.logout()
                message = "Sign in to start recording."
            }
        } else {
            message = "Sign in to start recording."
        }
    }

    func signIn(username: String, password: String, register: Bool) async {
        guard !username.isEmpty, !password.isEmpty else {
            message = "Enter a username and password."
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            user = try await api.login(username: username, password: password, register: register)
            message = "Ready"
            startRefreshing()
            await refreshRecordings()
        } catch {
            message = error.localizedDescription
        }
    }

    func signOut() async {
        await api.logout()
        user = nil
        recordings = []
        refreshTask?.cancel()
        message = "Signed out"
    }

    func toggleRecording() async {
        if isRecording {
            guard let url = recorder.stop() else { return }
            isRecording = false
            await upload(url: url, title: "Menu bar recording")
        } else {
            do {
                try recorder.start()
                isRecording = true
                message = "Recording…"
            } catch {
                message = error.localizedDescription
            }
        }
    }

    func upload(url: URL) async {
        let accessed = url.startAccessingSecurityScopedResource()
        defer {
            if accessed { url.stopAccessingSecurityScopedResource() }
        }
        await upload(url: url, title: url.deletingPathExtension().lastPathComponent)
    }

    func openDashboard() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:5173")!)
    }

    func refreshRecordings() async {
        guard backendOnline, user != nil else { return }
        do {
            recordings = try await api.recordings()
        } catch {
            message = error.localizedDescription
        }
    }

    private func upload(url: URL, title: String) async {
        isBusy = true
        message = "Uploading…"
        defer {
            isBusy = false
            try? FileManager.default.removeItem(at: url)
        }
        do {
            _ = try await api.upload(fileURL: url, title: title)
            message = "Processing in the background"
            await refreshRecordings()
        } catch {
            message = error.localizedDescription
        }
    }

    private func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                guard let self else { return }
                await self.refreshRecordings()
            }
        }
    }
}

// MARK: - Menu UI

struct MenuBarView: View {
    @ObservedObject var model: AppModel
    @State private var username = ""
    @State private var password = ""
    @State private var registering = false
    @State private var showingImporter = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Smoothie", systemImage: "waveform.circle.fill")
                    .font(.headline)
                Spacer()
                Circle()
                    .fill(model.backendOnline ? .green : .red)
                    .frame(width: 8, height: 8)
            }

            if !model.backendOnline {
                Text(model.message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Button("Retry backend") {
                    Task { await model.bootstrap() }
                }
                .buttonStyle(.borderedProminent)
            } else if !model.isAuthenticated {
                loginView
            } else {
                authenticatedView
            }

            Divider()
            HStack {
                Text(model.message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
        }
        .padding(16)
        .frame(width: 360)
        .fileImporter(
            isPresented: $showingImporter,
            allowedContentTypes: [.audio],
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await model.upload(url: url) }
            } else if case .failure(let error) = result {
                model.message = error.localizedDescription
            }
        }
    }

    private var loginView: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(registering ? "Create account" : "Sign in")
                .font(.title3.weight(.semibold))
            TextField("Username", text: $username)
                .textFieldStyle(.roundedBorder)
            SecureField("Password", text: $password)
                .textFieldStyle(.roundedBorder)
            Button(registering ? "Register" : "Sign in") {
                Task { await model.signIn(username: username, password: password, register: registering) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.isBusy)
            Button(registering ? "Already have an account? Sign in" : "Need an account? Register") {
                registering.toggle()
            }
            .buttonStyle(.link)
            .font(.caption)
        }
    }

    private var authenticatedView: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.user?.username ?? "Signed in")
                        .font(.subheadline.weight(.semibold))
                    Text("Quick capture")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Sign out") { Task { await model.signOut() } }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }

            HStack(spacing: 8) {
                Button {
                    Task { await model.toggleRecording() }
                } label: {
                    Label(model.isRecording ? "Stop & upload" : "Record", systemImage: model.isRecording ? "stop.fill" : "mic.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(model.isRecording ? .red : .accentColor)
                .disabled(model.isBusy && !model.isRecording)

                Button {
                    showingImporter = true
                } label: {
                    Label("Upload", systemImage: "arrow.up.doc")
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy || model.isRecording)
            }

            Button("Open full dashboard") { model.openDashboard() }
                .buttonStyle(.link)

            if model.recordings.isEmpty {
                Text("No recordings yet")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Text("Recent recordings")
                    .font(.subheadline.weight(.semibold))
                ForEach(model.recordings.prefix(5)) { recording in
                    RecordingRow(recording: recording)
                }
            }
        }
    }
}

struct RecordingRow: View {
    let recording: Recording

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: iconName)
                .foregroundStyle(color)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(recording.generatedTitle ?? recording.title ?? "Untitled")
                    .lineLimit(1)
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if isProcessing {
                ProgressView(value: recording.progress)
                    .frame(width: 46)
            }
        }
    }

    private var isProcessing: Bool {
        ["pending", "transcribing", "summarizing"].contains(recording.status)
    }

    private var statusText: String {
        if recording.status == "transcribing" {
            return "Transcribing \(Int(recording.progress * 100))%"
        }
        return recording.status.capitalized
    }

    private var iconName: String {
        switch recording.status {
        case "completed": return "checkmark.circle.fill"
        case "failed": return "exclamationmark.circle.fill"
        case "transcribing", "summarizing": return "ellipsis.circle.fill"
        default: return "clock.fill"
        }
    }

    private var color: Color {
        switch recording.status {
        case "completed": return .green
        case "failed": return .red
        default: return .secondary
        }
    }
}

// MARK: - Status bar application

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private var model: AppModel!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        model = AppModel()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "waveform.circle.fill", accessibilityDescription: "Smoothie")
            button.action = #selector(togglePopover)
            button.target = self
        }

        popover = NSPopover()
        popover.behavior = .transient
        popover.contentSize = NSSize(width: 360, height: 500)
        popover.contentViewController = NSHostingController(rootView: MenuBarView(model: model))
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.becomeKey()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        model?.signOutIfNeeded()
    }
}

private extension AppModel {
    func signOutIfNeeded() {
        backend.stop()
    }
}

@main
struct SmoothieMenuBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        Settings {
            EmptyView()
        }
    }
}
