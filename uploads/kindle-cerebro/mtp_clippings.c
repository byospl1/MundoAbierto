/*
 * mtp_clippings — descarga "My Clippings.txt" de un Kindle MTP en UNA sola
 * sesion USB (los Kindle Scribe abandonan el modo USB si se abren y cierran
 * varias sesiones seguidas, que es lo que hacen mtp-files + mtp-getfile).
 *
 * Uso:      mtp_clippings /ruta/destino.txt
 * Salida:   0 ok · 2 sin dispositivo · 3 no se pudo abrir · 4 sin archivo
 * Compilar: cc -O2 mtp_clippings.c -o mtp_clippings \
 *              $(pkg-config --cflags --libs libmtp)
 */
#include <libmtp.h>
#include <stdio.h>
#include <string.h>

static uint32_t buscar(LIBMTP_mtpdevice_t *dev, uint32_t storage,
                       uint32_t carpeta, const char *nombre, int es_carpeta) {
    uint32_t hallado = 0;
    LIBMTP_file_t *lista = LIBMTP_Get_Files_And_Folders(dev, storage, carpeta);
    for (LIBMTP_file_t *f = lista; f; f = f->next) {
        int carpeta_ok = (f->filetype == LIBMTP_FILETYPE_FOLDER);
        if (!hallado && f->filename &&
            strcasecmp(f->filename, nombre) == 0 &&
            carpeta_ok == es_carpeta)
            hallado = f->item_id;
    }
    while (lista) {
        LIBMTP_file_t *sig = lista->next;
        LIBMTP_destroy_file_t(lista);
        lista = sig;
    }
    return hallado;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "uso: mtp_clippings destino.txt\n"); return 1; }
    const char *destino = argv[1];

    LIBMTP_Init();
    LIBMTP_raw_device_t *crudos = NULL;
    int n = 0;
    if (LIBMTP_Detect_Raw_Devices(&crudos, &n) != LIBMTP_ERROR_NONE || n == 0) {
        fprintf(stderr, "NO_DEVICE\n");
        return 2;
    }

    LIBMTP_mtpdevice_t *dev = LIBMTP_Open_Raw_Device_Uncached(&crudos[0]);
    if (!dev) { fprintf(stderr, "NO_OPEN\n"); return 3; }

    int rc = 4;
    if (LIBMTP_Get_Storage(dev, LIBMTP_STORAGE_SORTBY_NOTSORTED) == 0) {
        for (LIBMTP_devicestorage_t *s = dev->storage; s && rc != 0; s = s->next) {
            /* raiz -> carpeta "documents" -> "My Clippings.txt" */
            uint32_t docs = buscar(dev, s->id,
                                   LIBMTP_FILES_AND_FOLDERS_ROOT,
                                   "documents", 1);
            uint32_t fid = 0;
            if (docs)
                fid = buscar(dev, s->id, docs, "My Clippings.txt", 0);
            if (!fid)   /* por si estuviera en la raiz */
                fid = buscar(dev, s->id, LIBMTP_FILES_AND_FOLDERS_ROOT,
                             "My Clippings.txt", 0);
            if (fid && LIBMTP_Get_File_To_File(dev, fid, destino, NULL, NULL) == 0) {
                printf("OK %s\n", destino);
                rc = 0;
            }
        }
    }
    if (rc != 0) {
        fprintf(stderr, "NO_FILE\n");
        LIBMTP_Dump_Errorstack(dev);
    }
    LIBMTP_Release_Device(dev);
    return rc;
}
