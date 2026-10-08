import AppKit
import ApplicationServices
import Vision
import ScreenCaptureKit

// Fixed JSON protocol. No shell or model-supplied AppleScript/JavaScript.
struct NativeFailure: Error { let code: String; let message: String }
func fail(_ code: String, _ message: String) throws -> Never { throw NativeFailure(code: code, message: message) }
func ax(_ element: AXUIElement, _ key: String) -> AnyObject? {
    var value: CFTypeRef?; AXUIElementCopyAttributeValue(element, key as CFString, &value); return value
}
func text(_ element: AXUIElement, _ key: String) -> String { return ax(element, key) as? String ?? "" }
func point(_ element: AXUIElement) -> CGPoint {
    var result = CGPoint.zero
    if let value = ax(element, kAXPositionAttribute), CFGetTypeID(value)==AXValueGetTypeID() { AXValueGetValue(value as! AXValue, .cgPoint, &result) }
    return result
}
func size(_ element: AXUIElement) -> CGSize {
    var result = CGSize.zero
    if let value = ax(element, kAXSizeAttribute), CFGetTypeID(value)==AXValueGetTypeID() { AXValueGetValue(value as! AXValue, .cgSize, &result) }
    return result
}
func trust() throws { if !AXIsProcessTrusted() { try fail("ACCESSIBILITY_PERMISSION_REQUIRED", "Enable Accessibility for the NOVA native helper or its launching terminal.") } }
func front() -> NSRunningApplication? { NSWorkspace.shared.frontmostApplication }
func root() throws -> AXUIElement { try trust(); guard let application = front() else { try fail("APP_NOT_FOUND", "No active application.") }; return AXUIElementCreateApplication(application.processIdentifier) }
func focused() throws -> AXUIElement { let app = try root(); guard let element = ax(app, kAXFocusedUIElementAttribute) else { try fail("ELEMENT_NOT_FOUND", "No focused UI element.") }; return element as! AXUIElement }
func secure(_ element: AXUIElement) -> Bool {
    if text(element, kAXRoleAttribute).contains("Secure") || text(element, kAXSubroleAttribute).contains("Secure") {return true}
    let metadata=text(element,kAXTitleAttribute)+" "+text(element,kAXDescriptionAttribute)+" "+text(element,"AXPlaceholderValue")
    return ["AXTextArea","AXTextField","AXComboBox"].contains(text(element,kAXRoleAttribute)) && metadata.range(of:#"\b(password|passcode|otp|2fa|pin|cvv|cvc|card number|credit card|verification code|security code|one.time.code)\b"#,options:[.regularExpression,.caseInsensitive]) != nil
}
func describe(_ element: AXUIElement, _ id: String) -> [String:Any] {
    let p = point(element), s = size(element), role = text(element, kAXRoleAttribute)
    let secret = secure(element)
    let editable = ["AXTextArea","AXTextField","AXComboBox"].contains(role)
    let value = secret ? "" : text(element, kAXValueAttribute)
    let label = [text(element, kAXTitleAttribute), text(element, kAXDescriptionAttribute), value].filter { !$0.isEmpty }.joined(separator: " ")
    return ["id":id,"role":role,"label":String(label.prefix(1000)),"value":editable ? String(value.prefix(1000)) : "","x":p.x,"y":p.y,"width":s.width,"height":s.height,"confidence":0.98,"source":"accessibility","editable":editable,"secure":secret]
}
func descendants(_ element: AXUIElement, _ prefix: String, _ depth: Int, _ result: inout [[String:Any]]) {
    if depth > 9 || result.count >= 180 { return }
    let item = describe(element, prefix)
    if prefix=="ax" || ((item["width"] as? CGFloat ?? 0)>0 && (item["height"] as? CGFloat ?? 0)>0 && (!((item["label"] as? String ?? "").isEmpty) || (item["editable"] as? Bool ?? false))) { result.append(item) }
    if let children = ax(element, kAXChildrenAttribute) as? [AXUIElement] {
        for (index, child) in children.prefix(80).enumerated() { descendants(child, prefix + "." + String(index), depth + 1, &result) }
    }
}
func locate(_ id: String) throws -> AXUIElement {
    var current = try root()
    let parts = id.split(separator: ".")
    guard parts.first == "ax" else { try fail("ELEMENT_NOT_FOUND", "Invalid accessibility target.") }
    for part in parts.dropFirst() {
        guard let index = Int(part), let children = ax(current, kAXChildrenAttribute) as? [AXUIElement], index >= 0, index < children.count else { try fail("ELEMENT_NOT_FOUND", "UI changed; observe again.") }
        current = children[index]
    }
    return current
}
func keycode(_ key: String) throws -> CGKeyCode {
    let keys: [String:CGKeyCode] = ["return":36,"enter":36,"escape":53,"tab":48,"space":49,"backspace":51,"delete":117,"left":123,"right":124,"down":125,"up":126,"command":55,"shift":56,"option":58,"control":59,"a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"o":31,"u":32,"i":34,"p":35,"l":37,"j":38,"k":40,"n":45,"m":46]
    guard let result = keys[key.lowercased()] else { try fail("KEY_UNSUPPORTED", "Unsupported key.") }; return result
}
func flags(_ keys: [String]) -> CGEventFlags {
    var result: CGEventFlags = []
    for key in keys { switch key.lowercased() { case "command","cmd":result.insert(.maskCommand); case "shift":result.insert(.maskShift); case "option","alt":result.insert(.maskAlternate); case "control","ctrl":result.insert(.maskControl); default:break } }
    return result
}
func emitKey(_ code: CGKeyCode, _ down: Bool, _ modifiers: CGEventFlags = []) { let event = CGEvent(keyboardEventSource:nil, virtualKey:code, keyDown:down); event?.flags = modifiers; event?.post(tap:.cghidEventTap) }
func checkTarget(_ args:[String:Any]) throws {
    if let target=args["target_pid"] as? Int32,target != front()?.processIdentifier {try fail("TARGET_CHANGED","The active application changed. Observe again before acting.")}
}
func moveCursor(_ target:CGPoint,_ duration:Double,_ args:[String:Any],drag:Bool=false) throws {
    let start=CGEvent(source:nil)?.location ?? target
    let steps=max(1,Int(min(1.5,max(0,duration))*60))
    for index in 1...steps {
        try checkTarget(args)
        let fraction=Double(index)/Double(steps),eased=fraction*fraction*(3-2*fraction)
        let point=CGPoint(x:start.x+(target.x-start.x)*eased,y:start.y+(target.y-start.y)*eased)
        CGEvent(mouseEventSource:nil,mouseType:drag ? .leftMouseDragged : .mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
        if steps>1 {RunLoop.current.run(until:Date().addingTimeInterval(duration/Double(steps)))}
    }
}

func run(_ action: String, _ args: [String:Any]) throws -> [String:Any] {
    let bounds = CGDisplayBounds(CGMainDisplayID())
    try checkTarget(args)
    switch action {
    case "permissions":
        return ["accessibility":AXIsProcessTrusted(),"screen_recording":CGPreflightScreenCaptureAccess(),"automation":"not_required","screen_width":bounds.width,"screen_height":bounds.height]
    case "permissions.request":
        var granted=false
        if args["kind"] as? String == "accessibility" { granted = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String:true] as CFDictionary) }
        else if args["kind"] as? String == "screen_recording" { granted = CGRequestScreenCaptureAccess() }
        return ["requested":true,"granted":granted]
    case "apps.list":
        return ["applications":NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }.map { ["name":$0.localizedName ?? "","bundle_id":$0.bundleIdentifier ?? "","pid":$0.processIdentifier] },"active_app":front()?.localizedName ?? ""]
    case "app.find":
        let name = args["name"] as? String ?? ""
        let known = [
            "Chrome":"com.google.Chrome", "Google Chrome":"com.google.Chrome",
            "Finder":"com.apple.finder", "TextEdit":"com.apple.TextEdit",
            "Safari":"com.apple.Safari", "Terminal":"com.apple.Terminal",
            "VS Code":"com.microsoft.VSCode", "Arc":"company.thebrowser.Browser",
            "WhatsApp":"net.whatsapp.WhatsApp", "Telegram":"ru.keepcoder.Telegram",
            "System Settings":"com.apple.systempreferences"
        ]
        let bundle = known[name] ?? name
        let candidates = [URL(fileURLWithPath:"/Applications/" + name + ".app"),URL(fileURLWithPath:NSHomeDirectory() + "/Applications/" + name + ".app"),URL(fileURLWithPath:"/System/Applications/" + name + ".app")]
        let found = NSWorkspace.shared.urlForApplication(withBundleIdentifier:bundle) ?? candidates.first { FileManager.default.fileExists(atPath:$0.path) }
        guard let found = found else { try fail("APP_NOT_INSTALLED", "The requested application is not installed.") }
        return ["path":found.path,"name":name]
    case "app.active":
        return ["active_app":front()?.localizedName ?? "","bundle_id":front()?.bundleIdentifier ?? "","pid":front()?.processIdentifier ?? 0]
    case "app.focus", "app.quit":
        let name = args["name"] as? String ?? ""
        let requestedPID=args["pid"] as? Int32
        let knownBundles = [
            "Safari":"com.apple.Safari", "Google Chrome":"com.google.Chrome", "Chrome":"com.google.Chrome",
            "Finder":"com.apple.finder", "TextEdit":"com.apple.TextEdit", "Terminal":"com.apple.Terminal",
            "VS Code":"com.microsoft.VSCode", "Arc":"company.thebrowser.Browser",
            "WhatsApp":"net.whatsapp.WhatsApp", "Telegram":"ru.keepcoder.Telegram",
            "System Settings":"com.apple.systempreferences"
        ]
        let requestedBundle = knownBundles[name] ?? name
        guard let app = NSWorkspace.shared.runningApplications.first(where:{ requestedPID != nil ? $0.processIdentifier == requestedPID! : (($0.bundleIdentifier ?? "") == requestedBundle || ($0.localizedName ?? "").caseInsensitiveCompare(name) == .orderedSame) }) else { try fail("APP_NOT_RUNNING", "Application is not running.") }
        let success = action == "app.quit" ? app.terminate() : app.activate(options:[.activateIgnoringOtherApps])
        if action == "app.focus" {RunLoop.current.run(until:Date().addingTimeInterval(0.15))}
        return ["success":success,"name":app.localizedName ?? name]
    case "observe":
        var result: [String:Any] = ["active_app":front()?.localizedName ?? "","active_bundle_id":front()?.bundleIdentifier ?? "","active_pid":front()?.processIdentifier ?? 0,"screen_width":bounds.width,"screen_height":bounds.height,"elements":[],"window_title":"","confidence":0.4]
        if AXIsProcessTrusted() && args["accessibility"] as? Bool != false {
            let app = try root(); var elements = [[String:Any]](); descendants(app,"ax",0,&elements)
            result["elements"] = elements; result["confidence"] = 0.98
            if let window = ax(app,kAXFocusedWindowAttribute) { result["window_title"] = text(window as! AXUIElement,kAXTitleAttribute) }
            if let element = ax(app,kAXFocusedUIElementAttribute) {
                let focusedElement=element as! AXUIElement
                var item=describe(focusedElement,"focused")
                item["description"]=secure(focusedElement) ? "[Secure field]" : text(focusedElement,kAXDescriptionAttribute)
                item["title"]=secure(focusedElement) ? "[Secure field]" : text(focusedElement,kAXTitleAttribute)
                if let selected=ax(focusedElement,kAXSelectedTextRangeAttribute),CFGetTypeID(selected)==AXValueGetTypeID() {
                    var range=CFRange(location:0,length:0)
                    if AXValueGetValue(selected as! AXValue,.cfRange,&range) {item["selection"]=["location":range.location,"length":range.length]}
                }
                result["focused_element"]=item
            }
        }
        return result
    case "ax.click":
        let element = try locate(args["element_id"] as? String ?? "")
        if secure(element) { try fail("CREDENTIAL_REQUIRED","Handle the secure field yourself.") }
        let status = AXUIElementPerformAction(element,kAXPressAction as CFString)
        if status != .success { try fail("ELEMENT_NOT_CLICKABLE","The requested element has no press action.") }
        return ["pressed":true]
    case "ax.type":
        let id=args["element_id"] as? String
        let element = id == nil || id == "focused" ? try? focused() : try? locate(id!)
        guard let element = element else { try fail("ELEMENT_NOT_FOUND","No text field was found.") }
        if secure(element) { try fail("CREDENTIAL_REQUIRED","Handle the secure field yourself.") }
        let value = args["text"] as? String ?? ""
        let status = AXUIElementSetAttributeValue(element,kAXValueAttribute as CFString,value as CFString)
        if status != .success { try fail("ELEMENT_NOT_EDITABLE","Text field cannot be set through accessibility.") }
        return ["typed":true,"text":text(element,kAXValueAttribute)]
    case "mouse.move", "mouse.click", "mouse.double_click", "mouse.right_click", "mouse.drag":
        try trust()
        let x = args["x"] as? Double ?? 0, y = args["y"] as? Double ?? 0
        guard bounds.contains(CGPoint(x:x,y:y)) else { try fail("COORDINATES_INVALID","Target is outside the main screen.") }
        let target = CGPoint(x:x,y:y), right = action == "mouse.right_click"
        let duration=args["movement_duration"] as? Double ?? 0.28
        try moveCursor(target,duration,args)
        if action == "mouse.move" {}
        else {
            if duration>0 {RunLoop.current.run(until:Date().addingTimeInterval(0.08));try checkTarget(args)}
            let down: CGEventType = right ? .rightMouseDown : .leftMouseDown, up: CGEventType = right ? .rightMouseUp : .leftMouseUp
            for index in 1...(action == "mouse.double_click" ? 2 : 1) {
                let first = CGEvent(mouseEventSource:nil,mouseType:down,mouseCursorPosition:target,mouseButton:right ? .right : .left)
                first?.setIntegerValueField(.mouseEventClickState,value:Int64(index)); first?.post(tap:.cghidEventTap)
                let end = action == "mouse.drag" ? CGPoint(x:args["to_x"] as? Double ?? x,y:args["to_y"] as? Double ?? y) : target
                guard bounds.contains(end) else { CGEvent(mouseEventSource:nil,mouseType:up,mouseCursorPosition:target,mouseButton:.left)?.post(tap:.cghidEventTap);try fail("COORDINATES_INVALID","Drag endpoint is outside the main screen.") }
                if action == "mouse.drag" {try moveCursor(end,duration,args,drag:true)}
                let last = CGEvent(mouseEventSource:nil,mouseType:up,mouseCursorPosition:end,mouseButton:right ? .right : .left)
                last?.setIntegerValueField(.mouseEventClickState,value:Int64(index));last?.post(tap:.cghidEventTap)
            }
        }
        return ["coordinates":[x,y]]
    case "mouse.release_all":
        let location=CGEvent(source:nil)?.location ?? .zero
        CGEvent(mouseEventSource:nil,mouseType:.leftMouseUp,mouseCursorPosition:location,mouseButton:.left)?.post(tap:.cghidEventTap)
        CGEvent(mouseEventSource:nil,mouseType:.rightMouseUp,mouseCursorPosition:location,mouseButton:.right)?.post(tap:.cghidEventTap)
        return ["released":true]
    case "mouse.position":
        let location=CGEvent(source:nil)?.location ?? .zero
        return ["x":location.x,"y":location.y]
    case "mouse.scroll":
        try trust(); CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:2,wheel1:Int32(args["dy"] as? Int ?? -500),wheel2:Int32(args["dx"] as? Int ?? 0),wheel3:0)?.post(tap:.cghidEventTap);return ["scrolled":true]
    case "keyboard.press", "keyboard.hotkey", "keyboard.key_down", "keyboard.key_up":
        try trust(); if action != "keyboard.key_up", let element = try? focused(), secure(element) { try fail("CREDENTIAL_REQUIRED","Handle secure input yourself.") }
        let code = try keycode(args["key"] as? String ?? "return"), modifiers = flags(args["modifiers"] as? [String] ?? [])
        let modifierNames=(args["modifiers"] as? [String] ?? []).map { ["cmd":"command","alt":"option","ctrl":"control"][$0.lowercased()] ?? $0.lowercased() }
        if action == "keyboard.hotkey" {for name in modifierNames {emitKey(try keycode(name),true,modifiers)}}
        if action != "keyboard.key_up" { emitKey(code,true,modifiers) }
        if action != "keyboard.key_down" { emitKey(code,false,modifiers) }
        if action == "keyboard.hotkey" {for name in modifierNames.reversed() {emitKey(try keycode(name),false)}}
        if action == "keyboard.hotkey" || action == "keyboard.press" {RunLoop.current.run(until:Date().addingTimeInterval(0.2))}
        return ["pressed":true]
    case "keyboard.type_text":
        try trust(); if let element = try? focused(), secure(element) { try fail("CREDENTIAL_REQUIRED","Handle secure input yourself.") }
        let unicode = Array((args["text"] as? String ?? "").utf16)
        var offset=0
        while offset<unicode.count {
            var end=min(offset+20,unicode.count)
            if end<unicode.count && unicode[end-1]>=0xD800 && unicode[end-1]<=0xDBFF {end-=1}
            let chunk = Array(unicode[offset..<end])
            for down in [true,false] { let event = CGEvent(keyboardEventSource:nil,virtualKey:0,keyDown:down);event?.flags=[]; event?.keyboardSetUnicodeString(stringLength:chunk.count,unicodeString:chunk);event?.post(tap:.cghidEventTap) }
            offset=end
        }
        RunLoop.current.run(until:Date().addingTimeInterval(0.2))
        return ["typed":true]
    case "keyboard.release_all":
        for code: CGKeyCode in [55,56,58,59,54,60,61,62] { emitKey(code,false) };return ["released":true]
    case "clipboard.read":return ["text":NSPasteboard.general.string(forType:.string) ?? ""]
    case "clipboard.write":NSPasteboard.general.clearContents(); let success = NSPasteboard.general.setString(args["text"] as? String ?? "",forType:.string);return ["success":success]
    case "ocr":
        let path = args["path"] as? String ?? ""
        let request = VNRecognizeTextRequest(); request.recognitionLevel = .accurate
        try VNImageRequestHandler(url:URL(fileURLWithPath:path)).perform([request])
        let items = (request.results ?? []).prefix(150).compactMap { observation -> [String:Any]? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            let width=args["width"] as? Double ?? bounds.width,height=args["height"] as? Double ?? bounds.height
            return ["id":"ocr-\(observation.uuid)","role":"text","label":candidate.string,"x":box.minX*width+(args["x"] as? Double ?? 0),"y":(1-box.maxY)*height+(args["y"] as? Double ?? 0),"width":box.width*width,"height":box.height*height,"confidence":Double(candidate.confidence),"source":"ocr"]
        }
        return ["elements":Array(items)]
    case "screen.capture":
        if !CGPreflightScreenCaptureAccess() { try fail("SCREEN_PERMISSION_REQUIRED","Enable Screen Recording for NOVA Computer Access.") }
        let destination = args["path"] as? String ?? ""
        var finished = false; var failure: Error?; var captured: CGImage?
        var captureFrame=bounds
        SCShareableContent.getExcludingDesktopWindows(true,onScreenWindowsOnly:true) { content,error in
            if let error = error { failure=error; finished=true; return }
            guard let display = content?.displays.first(where:{$0.displayID == CGMainDisplayID()}) else { finished=true; return }
            let window = content?.windows.filter {$0.owningApplication?.processID == front()?.processIdentifier && $0.isOnScreen && $0.frame.width > 100 && $0.frame.height > 100}.max(by: {$0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height})
            let filter = args["active_window"] as? Bool == true && window != nil ? SCContentFilter(desktopIndependentWindow:window!) : SCContentFilter(display:display,excludingWindows:[])
            if args["active_window"] as? Bool == true,let window=window {captureFrame=window.frame}
            let configuration = SCStreamConfiguration(); configuration.width=Int(captureFrame.width);configuration.height=Int(captureFrame.height);configuration.showsCursor=false
            SCScreenshotManager.captureImage(contentFilter:filter,configuration:configuration) { image,error in captured=image;failure=error;finished=true }
        }
        let deadline=Date().addingTimeInterval(10)
        while !finished && Date()<deadline { RunLoop.current.run(until:Date().addingTimeInterval(0.02)) }
        guard failure == nil, let image=captured, let png=NSBitmapImageRep(cgImage:image).representation(using:.png,properties:[:]) else { try fail("SCREEN_CAPTURE_FAILED","Could not capture the requested screen.") }
        try png.write(to:URL(fileURLWithPath:destination));return ["captured":true,"x":captureFrame.minX,"y":captureFrame.minY,"width":captureFrame.width,"height":captureFrame.height]
    default:try fail("NATIVE_ACTION_UNKNOWN","Unknown native action.")
    }
}
do {
    let data = CommandLine.arguments.count>1 ? Data(CommandLine.arguments[1].utf8) : FileHandle.standardInput.readDataToEndOfFile()
    let request = try JSONSerialization.jsonObject(with:data) as? [String:Any] ?? [:]
    if let outputPath=request["output_path"] as? String {
        try Data(String(ProcessInfo.processInfo.processIdentifier).utf8).write(to:URL(fileURLWithPath:outputPath+".pid"))
    }
    if request["action"] as? String == "watch.escape" {
        let monitor=CGEvent.tapCreate(tap:.cgSessionEventTap,place:.headInsertEventTap,options:.listenOnly,eventsOfInterest:CGEventMask(1)<<CGEventType.keyDown.rawValue,callback:{ _,type,event,_ in
            if type == .keyDown && event.getIntegerValueField(.keyboardEventKeycode)==53 {FileHandle.standardOutput.write(Data("{\"escape\":true}\n".utf8))}
            return Unmanaged.passUnretained(event)
        },userInfo:nil)
        if let monitor=monitor {
            let source=CFMachPortCreateRunLoopSource(kCFAllocatorDefault,monitor,0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(),source,.commonModes)
            CGEvent.tapEnable(tap:monitor,enable:true)
            CFRunLoopRun()
        }
        exit(0)
    }
    let value = try run(request["action"] as? String ?? "",request["arguments"] as? [String:Any] ?? [:])
    let output = try JSONSerialization.data(withJSONObject:["success":true,"data":value])
    if let outputPath=request["output_path"] as? String {try output.write(to:URL(fileURLWithPath:outputPath))}
    else {FileHandle.standardOutput.write(output)}
} catch {
    let failure = error as? NativeFailure
    let value: [String:Any] = ["success":false,"error":["code":failure?.code ?? "NATIVE_ACTION_FAILED","message":failure?.message ?? "The native operation failed."]]
    if let output = try? JSONSerialization.data(withJSONObject:value) {
        if CommandLine.arguments.count>1, let request=try? JSONSerialization.jsonObject(with:Data(CommandLine.arguments[1].utf8)) as? [String:Any], let path=request["output_path"] as? String {try? output.write(to:URL(fileURLWithPath:path))}
        else {FileHandle.standardOutput.write(output)}
    }
}
