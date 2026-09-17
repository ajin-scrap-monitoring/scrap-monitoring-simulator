export interface BitmapTargetSize {
  readonly width: number;
  readonly height: number;
}

export function cameraBitmapOptions(target: BitmapTargetSize): ImageBitmapOptions {
  if (
    !Number.isSafeInteger(target.width)
    || !Number.isSafeInteger(target.height)
    || target.width <= 0
    || target.height <= 0
  ) {
    throw new RangeError("camera bitmap target dimensions must be positive integers");
  }
  return {
    resizeWidth: target.width,
    resizeHeight: target.height,
    resizeQuality: "low",
  };
}
