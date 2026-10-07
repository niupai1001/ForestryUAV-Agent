"""Trusted worker source, embedded only in authorized offline preparation Jobs.

This code is never executed by the host. pip retains native PEP 517 isolation.
"""

WORKER = r'''
def build_sources(root, wheelhouse, sources, clean_env):
    import gzip, re, stat, tarfile
    from pathlib import PurePosixPath
    records, requirements = [], []
    entries, expanded, built_size, decoded_tar_remaining = 0, 0, 0, 640*1024*1024
    class BoundedTarReader:
        def __init__(self, reader):
            self.reader = reader
        def read(self, size):
            nonlocal decoded_tar_remaining
            data = self.reader.read(min(size, decoded_tar_remaining+1))
            decoded_tar_remaining -= len(data)
            if decoded_tar_remaining < 0:
                raise RuntimeError('Decoded tar stream exceeds limit')
            return data
    for index, source in enumerate(sources):
        archive_path = Path('/inputs')/source['resource_id']
        with archive_path.open('rb') as reader:
            data = reader.read(source['size_bytes']+1)
        if len(data) != source['size_bytes'] or hashlib.sha256(data).hexdigest() != source['sha256']:
            raise RuntimeError('Source changed after registration: '+source['filename'])
        del data
        destination_root = root/('source-'+str(index))
        destination_root.mkdir()
        seen = set()

        def unpack(name, directory, size, reader):
            nonlocal entries, expanded
            path = PurePosixPath(name)
            normalized = path.as_posix()
            entries += 1
            if (entries > 30000 or path.is_absolute() or '..' in path.parts or
                '\\' in name or '\x00' in name or ':' in name or normalized in seen or
                (not path.parts and not directory)):
                raise RuntimeError('Unsafe or excessive source archive member')
            seen.add(normalized)
            destination = destination_root.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(destination_root.resolve()):
                raise RuntimeError('Source archive path escaped')
            if directory:
                if size:
                    raise RuntimeError('Source archive directory has file data')
                destination.mkdir(parents=True, exist_ok=True)
                return
            if size < 0 or expanded + size > 512*1024*1024:
                raise RuntimeError('Source archive exceeds extraction limit')
            expanded += size
            destination.parent.mkdir(parents=True, exist_ok=True)
            remaining = size
            with destination.open('xb') as writer:
                while remaining:
                    chunk = reader.read(min(65536, remaining))
                    if not chunk:
                        raise RuntimeError('Truncated source archive member')
                    writer.write(chunk)
                    remaining -= len(chunk)
                if reader.read(1):
                    raise RuntimeError('Source archive member size mismatch')

        if source['filename'].endswith('.zip'):
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    mode = member.external_attr >> 16
                    if member.flag_bits & 1 or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise RuntimeError('Source archive links/special/encrypted files are forbidden')
                    with archive.open(member) as reader:
                        unpack(member.orig_filename, member.is_dir(), member.file_size, reader)
        else:
            with gzip.open(archive_path, 'rb') as compressed:
                stream = BoundedTarReader(compressed)
                with tarfile.open(fileobj=stream, mode='r|') as archive:
                    for member in archive:
                        if not (member.isfile() or member.isdir()):
                            raise RuntimeError('Source archive links/special files are forbidden')
                        if member.isdir():
                            unpack(member.name, True, member.size, None)
                        else:
                            with archive.extractfile(member) as reader:
                                unpack(member.name, False, member.size, reader)
                # Drain through gzip to validate its trailer/CRC, within the same
                # decoded-stream limit (including tar headers and padding).
                while stream.read(65536):
                    pass
        def is_project(path):
            return (path/'pyproject.toml').is_file() or (path/'setup.py').is_file()
        candidates = [destination_root] if is_project(destination_root) else [
            path for path in destination_root.iterdir() if path.is_dir() and is_project(path)]
        if len(candidates) != 1:
            raise RuntimeError('Source archive must contain one unambiguous Python project root')
        built = root/('built-'+str(index))
        built.mkdir()
        command = [sys.executable, '-I', '-B', '-m', 'pip', '--isolated',
            '--disable-pip-version-check', '--no-cache-dir', 'wheel', '--no-index',
            '--find-links', str(wheelhouse), '--no-deps', '--wheel-dir', str(built), str(candidates[0])]
        result = subprocess.run(command, env=clean_env, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        products = list(built.iterdir())
        if len(products) != 1:
            raise RuntimeError('Source build must produce exactly one wheel')
        product = products[0]
        if (product.is_symlink() or not product.is_file() or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,240}\.whl', product.name) or
            (wheelhouse/product.name).exists()):
            raise RuntimeError('Unsafe or duplicate built wheel')
        size = product.stat().st_size
        built_size += size
        if not size or built_size > 128*1024*1024:
            raise RuntimeError('Built wheels exceed archive limit')
        with product.open('rb') as reader:
            digest = hashlib.file_digest(reader, 'sha256').hexdigest()
        target = wheelhouse/product.name
        shutil.copyfile(product, target)
        shutil.copyfile(product, Path('/output')/product.name)
        requirements.append(str(target)+' --hash=sha256:'+digest)
        records.append({'source_resource_id':source['resource_id'], 'source_sha256':source['sha256'],
            'filename':product.name, 'sha256':digest, 'size_bytes':size})
    return requirements, records
'''
