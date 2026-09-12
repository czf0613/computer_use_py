import AppKit
import CoreGraphics

// Standalone synthetic UI. No event posting, clipboard inspection, or flag reset.
func screenID(_ screen: NSScreen?) -> UInt32 {
    (screen?.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
}

func globalRect(_ rect: NSRect) -> [String: Double] {
    let primary = NSScreen.screens.first { screenID($0) == CGMainDisplayID() }!
    return ["x": rect.minX, "y": primary.frame.maxY - rect.maxY,
            "width": rect.width, "height": rect.height]
}

func globalPoint(_ point: NSPoint) -> [String: Double] {
    let rect = globalRect(NSRect(origin: point, size: .zero))
    return ["x": rect["x"]!, "y": rect["y"]!]
}

final class Rows: NSView {
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        dirtyRect.fill()
        for row in 0..<70 {
            ("Scapkit synthetic scroll row \(row)" as NSString).draw(
                at: NSPoint(x: 12, y: row * 24),
                withAttributes: [.foregroundColor: NSColor.labelColor])
        }
    }
}

final class DragPad: NSView {
    var completed = 0
    var moves = 0
    var start: [String: Double] = [:]
    var end: [String: Double] = [:]
    var changed: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill()
        bounds.fill()
        ("Drag here — completed: \(completed)" as NSString).draw(
            at: NSPoint(x: 12, y: 16),
            withAttributes: [.foregroundColor: NSColor.labelColor])
    }
    override func mouseDown(with event: NSEvent) {
        start = globalPoint(window!.convertPoint(toScreen: event.locationInWindow))
        end = [:]
        moves = 0
        changed?()
    }
    override func mouseDragged(with event: NSEvent) {
        moves += 1
        changed?()
    }
    override func mouseUp(with event: NSEvent) {
        end = globalPoint(window!.convertPoint(toScreen: event.locationInWindow))
        completed += 1
        needsDisplay = true
        changed?()
    }
}

final class FixtureButton: NSButton {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

final class FixtureInput: NSTextField {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

final class Panel: NSObject, NSWindowDelegate {
    let name: String
    let window: NSWindow
    let input: NSTextField
    let button = FixtureButton(title: "Count plain click", target: nil, action: nil)
    let startButton = NSButton(title: "A + B prepared: start", target: nil, action: nil)
    let scroll = NSScrollView()
    let pad = DragPad()
    let editor = NSTextView()
    var clicks = 0
    var shortcuts: [String: Int] = [:]
    var changed: ((String) -> Void)?

    init(name: String, origin: NSPoint, width: CGFloat, instance: String) {
        self.name = name
        input = FixtureInput(string: "scapkit seed \(name)")
        window = NSWindow(contentRect: NSRect(origin: origin, size: NSSize(width: width, height: 400)),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        super.init()
        window.isReleasedWhenClosed = false
        window.title = "Scapkit modifiers \(name) — \(instance.prefix(8))"
        window.delegate = self
        // Exact-input assertions must not depend on the user's spelling and
        // text-replacement preferences. This editor belongs only to the fixture.
        editor.isFieldEditor = true
        editor.isAutomaticSpellingCorrectionEnabled = false
        editor.isAutomaticTextReplacementEnabled = false
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        let content = window.contentView!
        let label = NSTextField(labelWithString: "Synthetic window \(name) — no personal data")
        label.frame = NSRect(x: 16, y: 367, width: width - 32, height: 22)
        content.addSubview(label)
        input.frame = NSRect(x: 16, y: 320, width: width - 32, height: 30)
        input.font = .systemFont(ofSize: 16)
        content.addSubview(input)
        button.frame = NSRect(x: 16, y: 275, width: width - 32, height: 30)
        button.target = self
        button.action = #selector(clicked)
        content.addSubview(button)
        scroll.frame = NSRect(x: 16, y: 96, width: width - 32, height: 166)
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = Rows(frame: NSRect(x: 0, y: 0, width: width - 52, height: 1800))
        content.addSubview(scroll)
        pad.frame = NSRect(x: 16, y: 38, width: width - 32, height: 48)
        pad.changed = { [weak self] in self?.changed?("pad") }
        content.addSubview(pad)
        startButton.frame = NSRect(x: 16, y: 3, width: width - 32, height: 30)
        if name == "A" { content.addSubview(startButton) }
    }

    @objc func clicked() {
        clicks += 1
        button.title = "Plain clicks: \(clicks)"
        changed?("click")
    }
    func windowDidMove(_ notification: Notification) { changed?("moved") }
    func windowDidChangeScreen(_ notification: Notification) { changed?("screen_changed") }
    func windowDidBecomeKey(_ notification: Notification) { changed?("became_key") }
    func windowDidResignKey(_ notification: Notification) { changed?("resigned_key") }
    func windowWillReturnFieldEditor(_ sender: NSWindow, to client: Any?) -> Any? {
        (client as? NSTextField) === input ? editor : nil
    }

    func rect(_ view: NSView) -> [String: Double] {
        globalRect(window.convertToScreen(view.convert(view.bounds, to: nil)))
    }
    func state() -> [String: Any] {
        let editor = input.currentEditor() as? NSTextView
        let selection = editor?.selectedRange()
        let contentRect = window.convertToScreen(window.contentView!.bounds)
        let title = NSRect(x: window.frame.midX - 30, y: contentRect.maxY + 4,
                           width: 60, height: max(4, window.frame.maxY - contentRect.maxY - 8))
        return ["name": name, "number": window.windowNumber, "frame": globalRect(window.frame),
                "screen_id": screenID(window.screen), "is_key": window.isKeyWindow,
                "visible": window.isVisible, "input_focused": editor != nil && window.firstResponder === editor,
                "text": editor?.string ?? input.stringValue,
                "selection": selection.map { ["location": $0.location, "length": $0.length] } ?? [:],
                "clicks": clicks, "shortcuts": shortcuts, "scroll_y": scroll.contentView.bounds.minY,
                "drag": ["completed": pad.completed, "moves": pad.moves, "start": pad.start, "end": pad.end],
                "controls": ["input": rect(input), "button": rect(button), "scroll": rect(scroll),
                             "drag": rect(pad), "title": globalRect(title)]]
    }
}

final class Fixture: NSObject, NSApplicationDelegate {
    let output: URL
    let instance: String
    var panels: [Panel] = []
    var ready = false
    var sequence = 0
    var events: [[String: Any]] = []
    var timer: Timer?
    var monitor: Any?

    init(output: URL, instance: String) { self.output = output; self.instance = instance }

    func record(_ kind: String, window: String = "", event: NSEvent? = nil) {
        sequence += 1
        var row: [String: Any] = ["seq": sequence, "kind": kind, "window": window,
                                 "time": Date().timeIntervalSince1970, "active": NSApp.isActive,
                                 "key_window": panels.first { $0.window.isKeyWindow }?.name ?? ""]
        if let event = event {
            row["flags"] = event.modifierFlags.rawValue
            row["event_timestamp"] = event.timestamp
            row["point"] = globalPoint(event.window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation)
            if [.keyDown, .keyUp, .flagsChanged].contains(event.type) { row["key_code"] = event.keyCode }
            if [.keyDown, .keyUp].contains(event.type) { row["characters"] = event.characters ?? "" }
            if event.type == .scrollWheel { row["scroll_delta_y"] = event.scrollingDeltaY }
        }
        events.append(row)
        if events.count > 512 { events.removeFirst(events.count - 512) }
    }

    @objc func start() {
        guard !ready, let panel = panels.first else { return }
        panel.startButton.isEnabled = false
        panel.window.makeKeyAndOrderFront(nil)
        panel.window.makeFirstResponder(panel.input)
        if let editor = panel.input.currentEditor() as? NSTextView {
            editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        }
        ready = true
        record("ready")
        save()
    }

    @objc func shortcut(_ sender: NSMenuItem) {
        guard let panel = panels.first(where: { $0.window.isKeyWindow }), let id = sender.representedObject as? String else { return }
        panel.shortcuts[id, default: 0] += 1
        record("shortcut:\(id)", window: panel.name)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        guard let screen = NSScreen.screens.first(where: { screenID($0) == CGMainDisplayID() }) else { NSApp.terminate(nil); return }
        let area = screen.visibleFrame
        let width = min(440, (area.width - 60) / 2)
        guard width >= 300, area.height >= 460 else {
            fputs("Need at least 660 x 460 visible points for two baseline windows.\n", stderr)
            NSApp.terminate(nil)
            return
        }
        let left = area.midX - width - 10
        for (index, name) in ["A", "B"].enumerated() {
            let panel = Panel(name: name, origin: NSPoint(x: left + CGFloat(index) * (width + 20), y: area.midY - 200),
                              width: width, instance: instance)
            panels.append(panel)
            panel.changed = { [weak self] kind in self?.record(kind, window: name) }
            panel.window.orderFrontRegardless()
            panel.scroll.contentView.scroll(to: NSPoint(x: 0, y: 200))
            panel.scroll.reflectScrolledClipView(panel.scroll.contentView)
        }
        panels[0].startButton.target = self
        panels[0].startButton.action = #selector(start)
        let menu = NSMenu()
        let applicationItem = NSMenuItem()
        let applicationMenu = NSMenu(title: "Scapkit modifiers")
        applicationMenu.addItem(withTitle: "Quit Scapkit fixture", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu
        menu.addItem(applicationItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu = edit
        menu.addItem(editItem)
        let testsItem = NSMenuItem()
        let tests = NSMenu(title: "Synthetic shortcuts")
        let variants: [(String, NSEvent.ModifierFlags)] = [
            ("command", [.command]), ("shift", [.command, .shift]),
            ("option", [.command, .option]), ("control", [.command, .control]),
            ("all", [.command, .shift, .option, .control])]
        for (name, flags) in variants {
            let item = tests.addItem(withTitle: name, action: #selector(shortcut(_:)), keyEquivalent: "k")
            item.keyEquivalentModifierMask = flags
            item.target = self
            item.representedObject = name
        }
        testsItem.submenu = tests
        menu.addItem(testsItem)
        NSApp.mainMenu = menu
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .flagsChanged, .leftMouseDown,
                                                             .leftMouseUp, .leftMouseDragged, .scrollWheel]) { [weak self] event in
            guard let self = self else { return event }
            let name = self.panels.first { $0.window.windowNumber == event.windowNumber }?.name ?? ""
            self.record("event:\(event.type.rawValue)", window: name, event: event)
            return event
        }
        timer = Timer(timeInterval: 0.03, repeats: true) { [weak self] _ in self?.save() }
        RunLoop.main.add(timer!, forMode: .common)
        NSApp.activate(ignoringOtherApps: true)
        panels[0].window.makeKeyAndOrderFront(nil)
        save()
    }

    func save() {
        let state: [String: Any] = [
            "instance": instance, "pid": ProcessInfo.processInfo.processIdentifier,
            "time": Date().timeIntervalSince1970, "ready": ready, "active": NSApp.isActive,
            "pointer": globalPoint(NSEvent.mouseLocation),
            "key_window": panels.first { $0.window.isKeyWindow }?.name ?? "",
            "main_display_id": CGMainDisplayID(), "event_seq": sequence, "events": events,
            "screens": NSScreen.screens.map { ["id": screenID($0), "frame": globalRect($0.frame),
                                                "visible_frame": globalRect($0.visibleFrame), "scale": $0.backingScaleFactor] },
            "windows": Dictionary(uniqueKeysWithValues: panels.map { ($0.name, $0.state()) })]
        do {
            let data = try JSONSerialization.data(withJSONObject: state, options: [.sortedKeys])
            try data.write(to: output, options: .atomic)
        } catch {
            fputs("Scapkit fixture state write failed: \(error)\n", stderr)
            NSApp.terminate(nil)
        }
    }
    func applicationDidBecomeActive(_ notification: Notification) { record("app_active") }
    func applicationDidResignActive(_ notification: Notification) { record("app_inactive") }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

guard CommandLine.arguments.count == 4, CommandLine.arguments[1] == "--allow-desktop" else {
    fputs("Usage: modifier_fixture --allow-desktop STATE_PATH INSTANCE_ID\n", stderr)
    exit(2)
}
let app = NSApplication.shared
let fixture = Fixture(output: URL(fileURLWithPath: CommandLine.arguments[2]), instance: CommandLine.arguments[3])
app.delegate = fixture
app.run()
