from google.colab import drive
drive.mount('/content/drive')

gpu_info = !nvidia-smi
gpu_info = '\n'.join(gpu_info)
if gpu_info.find('failed') >= 0:
  print('Not connected to a GPU')
else:
  print(gpu_info)

!pip install huggingface_hub

from huggingface_hub import notebook_login

notebook_login()

# Commented out IPython magic to ensure Python compatibility.
# %%capture
# !apt install git-lfs

repo_name = "wav2vec2hindiasr_thefinal"

# !mv train.zip /content/drive/MyDrive/train.zip

# !mv test.zip /content/drive/MyDrive/test.zip

!unzip /content/drive/MyDrive/hinid_asr_data/train.zip -d .

!unzip /content/drive/MyDrive/hinid_asr_data/test.zip -d .

import pandas as pd

train_data = pd.read_csv('train/transcription.txt',header = None)
train_data.columns = ["label"]

test_data = pd.read_csv('test/transcription1.txt',header = None)
test_data.columns = ["label"]

train_data

train_data[['audio_path','text']] = train_data["label"].str.split(" ", 1, expand=True)
test_data[['audio_path','text']] = test_data["label"].str.split(" ", 1, expand=True)

train_data = train_data[["audio_path","text"]]
test_data = test_data[["audio_path","text"]]

train_data

test_data

train_data = train_data[:1500]
test_data = test_data[:200]

!pip install git+https://github.com/huggingface/datasets.git
!pip install git+https://github.com/huggingface/transformers.git
!pip install torchaudio
!pip install librosa
!pip install jiwer

# add file path

def add_file_path(text, train = True):
  if train:
    text = "train/audio/" + text + ".wav"
  else:
    text = "test/audio/" + text + ".wav"

  return text

# add file path
train_data['audio_path'] = train_data['audio_path'].map(lambda x: add_file_path(x))
test_data['audio_path'] = test_data['audio_path'].map(lambda x: add_file_path(x,train= False))

test_data

from datasets import Dataset, load_metric

train_data = Dataset.from_pandas(train_data)
test_data = Dataset.from_pandas(test_data)

def extract_all_chars(batch):
  all_text = " ".join(batch["text"])
  vocab = list(set(all_text))
  return {"vocab": [vocab], "all_text": [all_text]}

vocab_train = train_data.map(extract_all_chars, batched=True, batch_size=-1, keep_in_memory=True, remove_columns=train_data.column_names)
vocab_test = test_data.map(extract_all_chars, batched=True, batch_size=-1, keep_in_memory=True, remove_columns=test_data.column_names)

vocab_train

vocab_list = list(set(vocab_train["vocab"][0]) | set(vocab_test["vocab"][0]))

vocab_dict = {v: k for k, v in enumerate(vocab_list)}
print(vocab_dict)

vocab_dict["|"] = vocab_dict[" "]
del vocab_dict[" "]

vocab_dict["[UNK]"] = len(vocab_dict)
vocab_dict["[PAD]"] = len(vocab_dict)
print(len(vocab_dict))

import json
with open('vocab.json', 'w') as vocab_file:
    json.dump(vocab_dict, vocab_file)

from transformers import Wav2Vec2CTCTokenizer

tokenizer = Wav2Vec2CTCTokenizer("./vocab.json",  unk_token="[UNK]", pad_token="[PAD]", word_delimiter_token="|")

# tokenizer.push_to_hub(repo_name)

from transformers import Wav2Vec2FeatureExtractor

feature_extractor = Wav2Vec2FeatureExtractor(feature_size=1, sampling_rate=16000, padding_value=0.0, do_normalize=True, return_attention_mask=True)

from transformers import Wav2Vec2Processor

processor = Wav2Vec2Processor(feature_extractor=feature_extractor, tokenizer=tokenizer)

processor.save_pretrained("/content/drive/MyDrive/ASR_models/wav2vec2-large-xlsr-hindi-demo/")

train_data

import torchaudio

def speech_file_to_array_fn(batch):
    speech_array, sampling_rate = torchaudio.load(batch["audio_path"])
    batch["speech"] = speech_array[0].numpy()
    batch["sampling_rate"] = sampling_rate
    batch["target_text"] = batch["text"]
    return batch

train_data = train_data.map(speech_file_to_array_fn, remove_columns=train_data.column_names)
test_data = test_data.map(speech_file_to_array_fn, remove_columns=test_data.column_names)

import librosa
import numpy as np

def resample(batch):
    batch["speech"] = librosa.resample(np.asarray(batch["speech"]), 8000, 16_000)
    batch["sampling_rate"] = 16_000
    return batch

train_data = train_data.map(resample, num_proc=4)
test_data = test_data.map(resample, num_proc=4)

import IPython.display as ipd
import numpy as np
import random

rand_int = random.randint(0, len(train_data))

ipd.Audio(data=np.asarray(train_data[rand_int]["speech"]), autoplay=True, rate=16000)

# rand_int = random.randint(0, len(train_data))

print("Target text:", train_data[rand_int]["target_text"])
print("Input array shape:", np.asarray(train_data[rand_int]["speech"]).shape)
print("Sampling rate:", train_data[rand_int]["sampling_rate"])

def prepare_dataset(batch):
    # check that all files have the correct sampling rate
    assert (
        len(set(batch["sampling_rate"])) == 1
    ), f"Make sure all inputs have the same sampling rate of {processor.feature_extractor.sampling_rate}."

    batch["input_values"] = processor(batch["speech"], sampling_rate=batch["sampling_rate"][0]).input_values

    with processor.as_target_processor():
        batch["labels"] = processor(batch["target_text"]).input_ids
    return batch

train_data = train_data.map(prepare_dataset, remove_columns=train_data.column_names, batch_size=8, num_proc=4, batched=True)
test_data = test_data.map(prepare_dataset, remove_columns=test_data.column_names, batch_size=8, num_proc=4, batched=True)

import torch

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

@dataclass
class DataCollatorCTCWithPadding:
    """
    Data collator that will dynamically pad the inputs received.
    Args:
        processor (:class:`~transformers.Wav2Vec2Processor`)
            The processor used for proccessing the data.
        padding (:obj:`bool`, :obj:`str` or :class:`~transformers.tokenization_utils_base.PaddingStrategy`, `optional`, defaults to :obj:`True`):
            Select a strategy to pad the returned sequences (according to the model's padding side and padding index)
            among:
            * :obj:`True` or :obj:`'longest'`: Pad to the longest sequence in the batch (or no padding if only a single
              sequence if provided).
            * :obj:`'max_length'`: Pad to a maximum length specified with the argument :obj:`max_length` or to the
              maximum acceptable input length for the model if that argument is not provided.
            * :obj:`False` or :obj:`'do_not_pad'` (default): No padding (i.e., can output a batch with sequences of
              different lengths).
        max_length (:obj:`int`, `optional`):
            Maximum length of the ``input_values`` of the returned list and optionally padding length (see above).
        max_length_labels (:obj:`int`, `optional`):
            Maximum length of the ``labels`` returned list and optionally padding length (see above).
        pad_to_multiple_of (:obj:`int`, `optional`):
            If set will pad the sequence to a multiple of the provided value.
            This is especially useful to enable the use of Tensor Cores on NVIDIA hardware with compute capability >=
            7.5 (Volta).
    """

    processor: Wav2Vec2Processor
    padding: Union[bool, str] = True
    max_length: Optional[int] = None
    max_length_labels: Optional[int] = None
    pad_to_multiple_of: Optional[int] = None
    pad_to_multiple_of_labels: Optional[int] = None

    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        # split inputs and labels since they have to be of different lenghts and need
        # different padding methods
        input_features = [{"input_values": feature["input_values"]} for feature in features]
        label_features = [{"input_ids": feature["labels"]} for feature in features]

        batch = self.processor.pad(
            input_features,
            padding=self.padding,
            max_length=self.max_length,
            pad_to_multiple_of=self.pad_to_multiple_of,
            return_tensors="pt",
        )
        with self.processor.as_target_processor():
            labels_batch = self.processor.pad(
                label_features,
                padding=self.padding,
                max_length=self.max_length_labels,
                pad_to_multiple_of=self.pad_to_multiple_of_labels,
                return_tensors="pt",
            )

        # replace padding with -100 to ignore loss correctly
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        batch["labels"] = labels

        return batch

data_collator = DataCollatorCTCWithPadding(processor=processor, padding=True)

wer_metric = load_metric("wer")

def compute_metrics(pred):
    pred_logits = pred.predictions
    pred_ids = np.argmax(pred_logits, axis=-1)

    pred.label_ids[pred.label_ids == -100] = processor.tokenizer.pad_token_id

    pred_str = processor.batch_decode(pred_ids)
    # we do not want to group tokens when computing the metrics
    label_str = processor.batch_decode(pred.label_ids, group_tokens=False)

    wer = wer_metric.compute(predictions=pred_str, references=label_str)

    return {"wer": wer}

from transformers import Wav2Vec2ForCTC

model = Wav2Vec2ForCTC.from_pretrained(
    "theainerd/Wav2Vec2-large-xlsr-hindi",
    attention_dropout=0.2,
    hidden_dropout=0.2,
    feat_proj_dropout=0.0,
    mask_time_prob=0.05,
    layerdrop=0.1,
    gradient_checkpointing=True,
    ctc_loss_reduction="mean",
    pad_token_id=processor.tokenizer.pad_token_id,
    vocab_size=len(processor.tokenizer)
)

model.freeze_feature_extractor()

from transformers import TrainingArguments

training_args = TrainingArguments(
   output_dir=repo_name,
  group_by_length=True,
  per_device_train_batch_size=16,
  gradient_accumulation_steps=2,
  evaluation_strategy="steps",
  num_train_epochs=10,
  fp16=True,
  save_steps=400,
  eval_steps=400,
  logging_steps=400,
  learning_rate=1e-4,
  warmup_steps=500,
  save_total_limit=1,
  push_to_hub = True
)

from transformers import Trainer

trainer = Trainer(
    model=model,
    data_collator=data_collator,
    args=training_args,
    compute_metrics=compute_metrics,
    train_dataset=train_data,
    eval_dataset=test_data,
    tokenizer=processor.feature_extractor,
)

trainer.train()

trainer.push_to_hub()

model = Wav2Vec2ForCTC.from_pretrained(repo_name).to("cuda")
processor = Wav2Vec2Processor.from_pretrained(repo_name)

"""# TESTING"""

#model = Wav2Vec2ForCTC.from_pretrained("/content/drive/MyDrive/ASR_models/wav2vec2-large-xlsr-hindi-demo/checkpoint-1600").to("cuda")
#processor = Wav2Vec2Processor.from_pretrained("/content/drive/MyDrive/ASR_models/wav2vec2-large-xlsr-hindi-demo")

from datasets import load_dataset, load_metric
import re

wer = load_metric("wer")

common_voice_test_transcription = load_dataset("common_voice", "hi", data_dir="./cv-corpus-6.1-2020-12-11", split="test")

test_dataset = load_dataset("common_voice", "hi", split="test")

chars_to_ignore_regex = '[\,\?\.\!\-\;\:\"\“]'  # TODO: adapt this list to include all special characters you removed from the data
resampler = torchaudio.transforms.Resample(48_000, 16_000)

# Preprocessing the datasets.
# We need to read the aduio files as arrays
def speech_file_to_array_fn(batch):
	batch["sentence"] = re.sub(chars_to_ignore_regex, '', batch["sentence"]).lower()
	speech_array, sampling_rate = torchaudio.load(batch["path"])
	batch["speech"] = resampler(speech_array).squeeze().numpy()
	return batch

test_dataset = test_dataset.map(speech_file_to_array_fn)

# Preprocessing the datasets.
# We need to read the aduio files as arrays
def evaluate(batch):
    inputs = processor(batch["speech"], sampling_rate=16_000, return_tensors="pt", padding=True)

    with torch.no_grad():
        logits = model(inputs.input_values.to("cuda"), attention_mask=inputs.attention_mask.to("cuda")).logits
    
    pred_ids = torch.argmax(logits, dim=-1)
    batch["pred_strings"] = processor.batch_decode(pred_ids)
    return batch

result = test_dataset.map(evaluate, batched=True, batch_size=8)

print("WER: {:2f}".format(100 * wer.compute(predictions=result["pred_strings"], references=result["sentence"])))

