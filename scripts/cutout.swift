// Person cutout on-device (Apple Vision subject lifting) -> transparent PNG. No upload.
// usage: swift scripts/cutout.swift <in.jpg> <out.png>
import AppKit
import CoreImage
import Vision

let args = CommandLine.arguments
guard args.count == 3 else { fputs("usage: cutout.swift in out.png\n", stderr); exit(2) }
let inURL = URL(fileURLWithPath: args[1]), outURL = URL(fileURLWithPath: args[2])
guard let src = CIImage(contentsOf: inURL, options: [.applyOrientationProperty: true]) else {
    fputs("cannot read \(args[1])\n", stderr); exit(1)
}
let handler = VNImageRequestHandler(ciImage: src)
let req = VNGenerateForegroundInstanceMaskRequest()
try handler.perform([req])
guard let obs = req.results?.first else { fputs("no subject found\n", stderr); exit(1) }
let maskBuf = try obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: handler)
let mask = CIImage(cvPixelBuffer: maskBuf)
let clear = CIImage(color: .clear).cropped(to: src.extent)
let out = src.applyingFilter("CIBlendWithMask", parameters: [kCIInputBackgroundImageKey: clear, kCIInputMaskImageKey: mask])
let ctx = CIContext()
guard let cs = CGColorSpace(name: CGColorSpace.sRGB) else { exit(1) }
try ctx.writePNGRepresentation(of: out, to: outURL, format: .RGBA8, colorSpace: cs)
print("ok \(Int(src.extent.width))x\(Int(src.extent.height))")
