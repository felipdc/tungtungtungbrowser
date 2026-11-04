//! Standalone CPU-Lavapipe placement-log check; see docs/graphics-memory.md.
use metal2vulkan::metal::PixelFormat;
use metal2vulkan_runtime::{CommandBufferStatus, Device, ResourceOptions, TextureDescriptor};
use std::time::{Duration, Instant};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let started = Instant::now();
    let device = Device::system_default()?;
    // Use a CPU-only ICD manifest and expose no GPU devices to this process.
    if !device.name().to_ascii_lowercase().contains("llvmpipe") {
        return Err("This check requires CPU Lavapipe".into());
    }
    let queue = device.new_command_queue()?;
    let anchor = device.new_buffer(65536, ResourceOptions::shared())?;
    let anchor_pointer = anchor.contents().ok_or("Anchor is not CPU accessible")?;
    // SAFETY: no command uses the anchor and its coherent mapping remains alive.
    unsafe { std::ptr::write_bytes(anchor_pointer.as_ptr(), 0x5a, anchor.length() as usize) };
    let sizes = [256_u64, 4096, 65536, 1048576, 4194368];
    let mut checked = 0;
    for round in 0..4_u8 {
        for (index, size) in sizes.into_iter().enumerate() {
            let private = device.new_buffer(size, ResourceOptions::private())?;
            let output = device.new_buffer(size, ResourceOptions::shared())?;
            let pattern = 1 + round * 5 + index as u8;
            let command = queue.command_buffer()?;
            let mut blit = command.blit_command_encoder()?;
            blit.fill_buffer(&private, 0..size, pattern)?;
            blit.copy_from_buffer_to_buffer(&private, 0, &output, 0, size)?;
            blit.end_encoding()?;
            command.commit()?;
            if command
                .wait_until_completed_for(Duration::from_secs(15))
                .is_err()
                || command.status() != CommandBufferStatus::Completed
            {
                return Err(
                    "Original blit failed or exceeded its deadline; it was not repeated".into(),
                );
            }
            let pointer = output.contents().ok_or("Readback is not CPU accessible")?;
            // SAFETY: completion was verified; live coherent mappings cover these ranges.
            let bytes = unsafe { std::slice::from_raw_parts(pointer.as_ptr(), size as usize) };
            let neighbor = unsafe {
                std::slice::from_raw_parts(anchor_pointer.as_ptr(), anchor.length() as usize)
            };
            if !bytes.iter().all(|&byte| byte == pattern)
                || !neighbor.iter().all(|&byte| byte == 0x5a)
            {
                return Err(
                    "Readback or neighboring allocation contents changed unexpectedly".into(),
                );
            }
            // Exercise a small image placement too. This is allocation coverage,
            // not a texture-rendering or native Safari fidelity check.
            let descriptor = TextureDescriptor::texture_2d(PixelFormat::RGBA8Unorm, 32, 32, false);
            let texture = device.new_texture(&descriptor)?;
            drop(texture);
            checked += 1;
        }
    }
    drop((anchor, queue, device));
    println!(
        "{{\"status\":\"passed\",\"buffer_rounds\":{checked},\"small_image_allocations\":{checked},\"elapsed_seconds\":{},\"native_browser_verified\":false}}",
        started.elapsed().as_secs_f64()
    );
    Ok(())
}
