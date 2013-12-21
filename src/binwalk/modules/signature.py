import magic
import binwalk.core.parser
import binwalk.core.smart
from binwalk.core.compat import *
from binwalk.core.module import Module, Option, Kwarg

class Signature(Module):

	TITLE = "Signature Scan"

	CLI = [
			Option(short='B',
				   long='signature',
				   kwargs={'enabled' : True},
				   description='Scan target file(s) for file signatures'),
			Option(short='R',
				   long='raw-bytes',
				   kwargs={'raw_bytes' : None},
				   type=str,
				   description='Scan target file(s) for the specified sequence of bytes'),
			Option(short='A',
				   long='opcodes',
				   kwargs={'enabled' : True, 'search_for_opcodes' : True},
				   description='Scan target file(s) for common executable opcodes'),
			Option(short='m',
				   long='magic',
				   kwargs={'magic_files' : []},
				   type=list,
				   dtype='file',
				   description='Specify a custom magic file to use'),
			Option(short='b',
				   long='dumb',
				   kwargs={'dumb_scan' : True},
				   description='Disable smart signature keywords'),
	]

	KWARGS = [
			Kwarg(name='enabled', default=False),
			Kwarg(name='raw_bytes', default=None),
			Kwarg(name='search_for_opcodes', default=False),
			Kwarg(name='dumb_scan', default=False),
			Kwarg(name='magic_files', default=[]),
	]

	MAGIC_FLAGS = magic.MAGIC_NO_CHECK_TEXT | magic.MAGIC_NO_CHECK_ENCODING | magic.MAGIC_NO_CHECK_APPTYPE | magic.MAGIC_NO_CHECK_TOKENS

	def init(self):
		# Create SmartSignature and MagicParser class instances. These are mostly for internal use.
		self.smart = binwalk.core.smart.SmartSignature(self.config.filter, ignore_smart_signatures=self.dumb_scan)
		self.parser = binwalk.core.parser.MagicParser(self.config.filter, self.smart)

		# If a raw byte sequence was specified, build a magic file from that instead of using the default magic files
		if self.raw_bytes is not None:
			self.magic_files = [self.parser.file_from_string(self.raw_bytes)]

		# Use the system default magic file if no other was specified
		if not self.magic_files:
			if self.search_for_opcodes:
				self.magic_files = [
					self.config.settings.paths['user'][self.config.settings.BINARCH_MAGIC_FILE],
					self.config.settings.paths['system'][self.config.settings.BINARCH_MAGIC_FILE],
				]
			else:
				# Append the user's magic file first so that those signatures take precedence
				self.magic_files = [
					self.config.settings.paths['user'][self.config.settings.BINWALK_MAGIC_FILE],
					self.config.settings.paths['system'][self.config.settings.BINWALK_MAGIC_FILE],
				]

		# Parse the magic file(s) and initialize libmagic
		self.mfile = self.parser.parse(self.magic_files)
		self.magic = magic.open(self.MAGIC_FLAGS)
		self.magic.load(str2bytes(self.mfile))
		
		# Once the temporary magic files are loaded into libmagic, we don't need them anymore; delete the temp files
		self.parser.rm_magic_files()

	def validate(self, r):
		'''
		Called automatically by self.result.
		'''
		if not r.description:
			r.valid = False

		if r.size and (r.size + r.offset) > r.file.size:
			r.valid = False

		if r.jump and (r.jump + r.offset) > r.file.size:
			r.valid = False

	def scan_file(self, fp):
		current_file_offset = 0

		while True:
			(data, dlen) = fp.read_block()
			if not data:
				break

			current_block_offset = 0
			block_start = fp.offset + fp.total_read - dlen
			self.status.completed = block_start - fp.offset

			for candidate_offset in self.parser.find_signature_candidates(data, dlen):

				# current_block_offset is set when a jump-to-offset keyword is encountered while
				# processing signatures. This points to an offset inside the current data block
				# that scanning should jump to, so ignore any subsequent candidate signatures that
				# occurr before this offset inside the current data block.
				if candidate_offset < current_block_offset:
					continue

				# In python3 we need a bytes object to pass to magic.buffer
				candidate_data = str2bytes(data[candidate_offset:candidate_offset+fp.MAX_TRAILING_SIZE])
			
				# Pass the data to libmagic, and split out multiple results into a list
				magic_result = self.magic.buffer(candidate_data)

				if self.config.filter.valid_result(magic_result):
					# The smart filter parser returns a binwalk.core.module.Result object
					r = self.smart.parse(magic_result)

					# Set the absolute offset inside the target file
					r.offset = block_start + candidate_offset + r.adjust
					# Provide an instance of the current file object
					r.file = fp
		
					# Register the result for futher processing/display
					self.result(r=r)
					
					# Is this a valid result and did it specify a jump-to-offset keyword?
					if r.valid and r.jump > 0:
						absolute_jump_offset = r.offset + r.jump
						current_block_offset = candidate_offset + r.jump

						# If the jump-to-offset is beyond the confines of the current block, seek the file to
						# that offset and quit processing this block of data.
						if absolute_jump_offset >= fp.tell():
							fp.seek(r.offset + r.jump)
							break

	def run(self):
		target_files = self.config.target_files

		while target_files:
			for fp in target_files:
				self.header()
			
				self.status.clear()
				self.status.total = fp.length
				self.status.completed = 0

				self.scan_file(fp)
			
				self.footer()

			# Add any pending extracted files to the target_files list and reset the extractor's pending file list
			target_files = [self.config.open_file(f) for f in self.extractor.pending]
			self.extractor.pending = []
