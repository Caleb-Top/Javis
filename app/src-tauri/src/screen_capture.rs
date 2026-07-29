use base64::{engine::general_purpose::STANDARD, Engine as _};
use windows::{
    core::Interface,
    Win32::{
        Foundation::HMODULE,
        Graphics::{
            Direct3D::D3D_DRIVER_TYPE_HARDWARE,
            Direct3D11::{
                D3D11CreateDevice, D3D11_CPU_ACCESS_READ, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                D3D11_MAP_READ, D3D11_MAPPED_SUBRESOURCE, D3D11_SDK_VERSION,
                D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING, ID3D11Device, ID3D11DeviceContext,
                ID3D11Texture2D,
            },
            Dxgi::{
                IDXGIAdapter, IDXGIDevice, IDXGIOutput1, IDXGIResource,
                DXGI_OUTDUPL_FRAME_INFO,
            },
            Gdi::{
                BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject,
                GetDC, GetDIBits, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
                CAPTUREBLT, DIB_RGB_COLORS, HGDIOBJ, ROP_CODE, SRCCOPY,
            },
        },
        UI::WindowsAndMessaging::{
            GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
            SM_YVIRTUALSCREEN,
        },
    },
};

type ScreenPixels = (u32, u32, Vec<u8>);

pub fn capture_screen_native() -> Result<String, String> {
    let (width, height, pixels, backend) = match capture_gdi() {
        Ok((width, height, pixels)) => (width, height, pixels, "windows-gdi"),
        Err(gdi_error) => match capture_dxgi() {
            Ok((width, height, pixels)) => {
                (width, height, pixels, "windows-dxgi-duplication")
            }
            Err(dxgi_error) => {
                return Err(format!(
                    "native screen capture failed; GDI: {gdi_error}; DXGI: {dxgi_error}"
                ));
            }
        },
    };
    Ok(format!(
        "{{\"width\":{width},\"height\":{height},\"backend\":\"{backend}\",\"bgraBase64\":\"{}\"}}",
        STANDARD.encode(pixels)
    ))
}

fn capture_gdi() -> Result<ScreenPixels, String> {
    unsafe {
        let left = GetSystemMetrics(SM_XVIRTUALSCREEN);
        let top = GetSystemMetrics(SM_YVIRTUALSCREEN);
        let width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        let height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        if width <= 0 || height <= 0 {
            return Err("Windows returned an invalid virtual screen size".into());
        }

        let screen_dc = GetDC(None);
        if screen_dc.0.is_null() {
            return Err("GetDC failed".into());
        }
        let memory_dc = CreateCompatibleDC(Some(screen_dc));
        if memory_dc.0.is_null() {
            ReleaseDC(None, screen_dc);
            return Err("CreateCompatibleDC failed".into());
        }
        let bitmap = CreateCompatibleBitmap(screen_dc, width, height);
        if bitmap.0.is_null() {
            let _ = DeleteDC(memory_dc);
            ReleaseDC(None, screen_dc);
            return Err("CreateCompatibleBitmap failed".into());
        }
        let old_object = SelectObject(memory_dc, HGDIOBJ(bitmap.0));
        let copied = BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            Some(screen_dc),
            left,
            top,
            ROP_CODE(SRCCOPY.0 | CAPTUREBLT.0),
        );
        if let Err(error) = copied {
            SelectObject(memory_dc, old_object);
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            let _ = DeleteDC(memory_dc);
            ReleaseDC(None, screen_dc);
            return Err(format!("BitBlt failed: {error}"));
        }

        let mut bitmap_info = BITMAPINFO::default();
        bitmap_info.bmiHeader = BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB.0,
            ..Default::default()
        };
        let mut pixels = vec![0_u8; width as usize * height as usize * 4];
        let rows = GetDIBits(
            memory_dc,
            bitmap,
            0,
            height as u32,
            Some(pixels.as_mut_ptr().cast()),
            &mut bitmap_info,
            DIB_RGB_COLORS,
        );
        SelectObject(memory_dc, old_object);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory_dc);
        ReleaseDC(None, screen_dc);

        if rows == 0 {
            return Err("GetDIBits failed".into());
        }
        Ok((width as u32, height as u32, pixels))
    }
}

fn capture_dxgi() -> Result<ScreenPixels, String> {
    unsafe {
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        D3D11CreateDevice(
            None::<&IDXGIAdapter>,
            D3D_DRIVER_TYPE_HARDWARE,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None,
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )
        .map_err(|error| format!("D3D11CreateDevice failed: {error}"))?;
        let device = device.ok_or("D3D11 device was not returned")?;
        let context = context.ok_or("D3D11 context was not returned")?;
        let dxgi_device: IDXGIDevice = device
            .cast()
            .map_err(|error| format!("IDXGIDevice cast failed: {error}"))?;
        let adapter = dxgi_device
            .GetAdapter()
            .map_err(|error| format!("GetAdapter failed: {error}"))?;
        let output = adapter
            .EnumOutputs(0)
            .map_err(|error| format!("EnumOutputs failed: {error}"))?;
        let output: IDXGIOutput1 = output
            .cast()
            .map_err(|error| format!("IDXGIOutput1 cast failed: {error}"))?;
        let duplication = output
            .DuplicateOutput(&device)
            .map_err(|error| format!("DuplicateOutput failed: {error}"))?;

        let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut desktop_resource: Option<IDXGIResource> = None;
        duplication
            .AcquireNextFrame(2_000, &mut frame_info, &mut desktop_resource)
            .map_err(|error| format!("AcquireNextFrame failed: {error}"))?;
        let result = (|| -> Result<ScreenPixels, String> {
            let texture: ID3D11Texture2D = desktop_resource
                .ok_or("DXGI did not return a desktop frame")?
                .cast()
                .map_err(|error| format!("ID3D11Texture2D cast failed: {error}"))?;
            let mut description = D3D11_TEXTURE2D_DESC::default();
            texture.GetDesc(&mut description);
            if description.Width == 0 || description.Height == 0 {
                return Err("DXGI returned an invalid frame size".into());
            }
            description.Usage = D3D11_USAGE_STAGING;
            description.BindFlags = 0;
            description.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
            description.MiscFlags = 0;
            let mut staging: Option<ID3D11Texture2D> = None;
            device
                .CreateTexture2D(&description, None, Some(&mut staging))
                .map_err(|error| format!("CreateTexture2D failed: {error}"))?;
            let staging = staging.ok_or("D3D11 staging texture was not returned")?;
            context.CopyResource(&staging, &texture);

            let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
            context
                .Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
                .map_err(|error| format!("D3D11 Map failed: {error}"))?;
            let row_bytes = description.Width as usize * 4;
            let mut pixels = vec![0_u8; row_bytes * description.Height as usize];
            for row in 0..description.Height as usize {
                let source = std::slice::from_raw_parts(
                    (mapped.pData as *const u8).add(row * mapped.RowPitch as usize),
                    row_bytes,
                );
                let start = row * row_bytes;
                pixels[start..start + row_bytes].copy_from_slice(source);
            }
            context.Unmap(&staging, 0);
            Ok((description.Width, description.Height, pixels))
        })();
        let _ = duplication.ReleaseFrame();
        result
    }
}
