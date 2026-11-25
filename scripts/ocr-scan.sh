#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
    echo "Uso: $0 ESCANEO [DIRECTORIO_SALIDA] [IDIOMAS]" >&2
    echo "Idiomas por defecto: cat+spa. Ejemplos: eng, fra, cat+spa." >&2
    exit 2
fi

input=$1
if [ ! -f "$input" ]; then
    echo "No existe el escaneo: $input" >&2
    exit 2
fi

input_dir=$(cd "$(dirname "$input")" && pwd -P)
input_name=$(basename "$input")
output_dir=${2:-$input_dir}
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
languages=${3:-cat+spa}

case $(printf '%s' "${input_name##*.}" | tr '[:upper:]' '[:lower:]') in
    pdf) kind=pdf ;;
    png|jpg|jpeg|tif|tiff|bmp) kind=image ;;
    *) echo "Formato no admitido: $input_name (PDF, PNG, JPEG, TIFF o BMP)" >&2; exit 2 ;;
esac

base=${input_name%.*}
pdf_name=$base-ocr.pdf
text_name=$base-ocr.txt
if [ -e "$output_dir/$pdf_name" ] || [ -e "$output_dir/$text_name" ]; then
    echo "La salida ya existe en $output_dir; no se sobrescribe." >&2
    exit 2
fi

script_dir=$(cd "$(dirname "$0")" && pwd -P)
project_dir=$(cd "$script_dir/.." && pwd -P)
image=martina-edu-ocr
if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" - < "$project_dir/Dockerfile.ocr"
fi

# Los archivos originales se montan solo para lectura y el OCR funciona sin red.
if [ "$kind" = pdf ]; then
    docker run --rm --network none --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        --mount "type=bind,src=$input_dir,dst=/input,readonly" \
        --mount "type=bind,src=$output_dir,dst=/output" \
        "$image" ocrmypdf --skip-text --rotate-pages --deskew \
        -l "$languages" "/input/$input_name" "/output/$pdf_name"
else
    docker run --rm --network none --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        --mount "type=bind,src=$input_dir,dst=/input,readonly" \
        --mount "type=bind,src=$output_dir,dst=/output" \
        "$image" tesseract "/input/$input_name" "/output/$base-ocr" \
        -l "$languages" pdf
fi

docker run --rm --network none --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    --mount "type=bind,src=$output_dir,dst=/output" \
    "$image" pdftotext -layout "/output/$pdf_name" "/output/$text_name"

echo "PDF: $output_dir/$pdf_name"
echo "Texto: $output_dir/$text_name"
