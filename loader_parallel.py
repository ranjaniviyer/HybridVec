import numpy as np
import traceback
import string
import linecache
import torch
from collections import OrderedDict
from definitions import get_a_definition
from torch.utils.data import Dataset, DataLoader
import pdb
import mmap
from multiprocessing import Manager

MAX_CACHED_LINES=50
PUNC = set(string.punctuation)
def clean_str(string):
    return "".join([c for c in string.lower() if c not in PUNC])

class DefinitionsDataset(Dataset):

  def __init__(self, vocab_file_name, vocab):
    with open(vocab_file_name, "r") as f:
      self.vocab_file = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    self.process_manager = Manager()
    self.shared_mem = self.process_manager.dict()
    self.shared_mem['idx_offset'] = 0
    self.shared_mem['oldest_line'] = 0
    self.shared_mem['at_file_line'] = 0
    self.vocab_len = len(vocab.stoi)
    self.vocab = vocab
    for i in range(MAX_CACHED_LINES-1):
        self.get_vocab_pair(i)

  def __len__(self):
    return self.vocab_len

  def __getitem__(self, idx):
    """
    Return (definition, embedding)
    """
    word,embedding = self.get_vocab_pair(idx + self.shared_mem['idx_offset'])
    definition = None
    while definition is None:
      self.shared_mem['idx_offset'] += 1
      word,embedding = self.get_vocab_pair(idx + self.shared_mem['idx_offset'])
      definition = get_a_definition(word)
      if definition is None:
          continue
      try:
        words = [clean_str(word) for word in definition.split(' ')]
        definition = []
        for i,word in enumerate(words):
            if word in self.vocab.stoi:
                definition.append(self.vocab.stoi[word])
            else:
                definition.append(0)
      except Exception as e:
        print('Error in lookup')
        traceback.print_exc()
        definition = None
    return (np.array(definition), embedding.astype(np.float32))

  def get_vocab_pair(self, idx):
    if idx in self.shared_mem:
      return self.shared_mem[idx]
    word = None
    while word is None:
        line = self.vocab_file.readline().decode().strip()
        self.shared_mem['at_file_line']+=1
        splitLine = line.split(' ')
        if len(line) == 0:
            self.shared_mem['idx_offset'] += 1
            continue
        try:
            word = splitLine[0]
            embedding = np.array([float(val) for val in splitLine[1:]])
        except Exception as e:
            print(e)
            self.shared_mem['idx_offset'] += 1
    ret = (word, embedding)
    self.shared_mem[self.shared_mem['at_file_line']] = ret
    if idx > self.shared_mem['oldest_line']:
        self.shared_mem['oldest_line'] = idx

    return ret

def collate_fn(data):
    """Creates mini-batch tensors from the list of tuples (src_seq, trg_seq).
    We should build a custom collate_fn rather than using default collate_fn,
    because merging sequences (including padding) is not supported in default.
    Seqeuences are padded to the maximum length of mini-batch sequences (dynamic padding).
    Args:
        data: list of tuple (src_seq, trg_seq).
            - src_seq: torch tensor of shape (?); variable length.
            - trg_seq: torch tensor of shape (?); variable length.
    Returns:
        src_seqs: torch tensor of shape (batch_size, padded_length).
        src_lengths: list of length (batch_size); valid length for each padded source sequence.
        trg_seqs: torch tensor of shape (batch_size, padded_length).
    """
    def merge(sequences):
        lengths = [len(seq) for seq in sequences]
        padded_seqs = torch.zeros(len(sequences), max(lengths)).long()
        for i, seq in enumerate(sequences):
            end = lengths[i]
            padded_seqs[i, :end] = torch.from_numpy(seq[:end])
        return padded_seqs, lengths

    # sort a list by sequence length (descending order) to use pack_padded_sequence
    data.sort(key=lambda x: len(x[0]), reverse=True)

    # seperate source and target sequences
    src_seqs, trg_seqs = zip(*data)

    # merge sequences (from tuple of 1D tensor to 2D tensor)
    src_seqs, src_lengths = merge(src_seqs)
    trg_seqs = torch.from_numpy(np.stack(trg_seqs, axis = 0))

    return src_seqs, src_lengths, trg_seqs


def get_data_loader(vocab_file, vocab, batch_size=8, num_workers=8):
  dataset = DefinitionsDataset(vocab_file, vocab)
  return DataLoader(dataset, 
                    batch_size=batch_size, 
                    num_workers=num_workers, 
                    collate_fn=collate_fn,
                    shuffle=False)